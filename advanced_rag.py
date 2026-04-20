import os
import yaml
import pandas as pd
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# --- NEW: Use Unstructured instead of PyPDFLoader ---
from langchain_community.document_loaders import UnstructuredPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter

# LLM & Embeddings
import google.generativeai as genai
from langchain_google_genai import ChatGoogleGenerativeAI

# --- MONKEYPATCH GOOGLE GENAI TO FIX RAGAS ---
original_gca = genai.GenerativeModel.generate_content_async
async def patched_gca(self, contents, **kwargs):
    kwargs.pop("temperature", None)
    return await original_gca(self, contents, **kwargs)
genai.GenerativeModel.generate_content_async = patched_gca

original_gc = genai.GenerativeModel.generate_content
def patched_gc(self, contents, **kwargs):
    kwargs.pop("temperature", None)
    return original_gc(self, contents, **kwargs)
genai.GenerativeModel.generate_content = patched_gc
# -----------------------------------------------
from langchain_huggingface import HuggingFaceEmbeddings

# Retrievers & Vectorstores
from langchain_community.vectorstores import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever
from langchain.retrievers.contextual_compression import ContextualCompressionRetriever
from langchain_cohere import CohereRerank

# Chains
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate

# --- NEW: Ragas Evaluation Library ---
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    Faithfulness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
)
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper

# config and download env vars
load_dotenv()
with open("config.yaml", "r", encoding="utf-8") as file:
    config = yaml.safe_load(file)

global_chunks = []
global_rag_chain = None
global_llm = None
global_embeddings = None



def setup_ragas_metrics():
    """Configure Ragas to use the system's Gemini LLM and HuggingFace Embeddings instead of default OpenAI"""
    ragas_llm = LangchainLLMWrapper(global_llm)
    ragas_emb = LangchainEmbeddingsWrapper(global_embeddings)
    
    # Initialize metrics with system's LLM (Ragas v0.2+ requires instantiation)
    return [
        Faithfulness(llm=ragas_llm),
        AnswerRelevancy(llm=ragas_llm, embeddings=ragas_emb),
        ContextPrecision(llm=ragas_llm),
        ContextRecall(llm=ragas_llm),
    ]

def ingest_and_rebuild_rag(pdf_path: str):
    global global_chunks
    global global_rag_chain
    global global_llm
    global global_embeddings

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Do not find: {pdf_path}")

    print(f"[1/5] Reading PDF with Unstructured: {pdf_path}...")
    # USE UNSTRUCTURED: Deep analysis of tables, 2-column layout. 
    # Use strategy="hi_res" to activate OCR for diagrams/images with text.
    loader = UnstructuredPDFLoader(pdf_path, strategy="hi_res")
    docs = loader.load()

    print("[2/5] Splitting text (Recursive Character Splitter)...")
    global_embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n\n", "\n", ".", " "]
    )
    chunks = text_splitter.split_documents(docs)
    global_chunks.extend(chunks)

    print(f"[3/5] Setting up Hybrid Search (Total Chunks: {len(global_chunks)})...")
    vectorstore = Chroma.from_documents(global_chunks, global_embeddings)
    vector_retriever = vectorstore.as_retriever(search_kwargs={"k": config['retrieval']['vector_search_k']})

    bm25_retriever = BM25Retriever.from_documents(global_chunks)
    bm25_retriever.k = config['retrieval']['bm25_search_k']

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, vector_retriever], 
        weights=config['retrieval']['ensemble_weights']
    )

    print("[4/5] Setting up Cohere Re-ranker...")
    compressor = CohereRerank(
        model="rerank-multilingual-v3.0",
        top_n=config['retrieval']['rerank_top_n']
    )
    compression_retriever = ContextualCompressionRetriever(
        base_compressor=compressor, 
        base_retriever=ensemble_retriever
    )

    print("[5/5] Setting up Google Gemini LLM...")
    global_llm = ChatGoogleGenerativeAI(
        model=config['model']['llm_name'],
        temperature=config['model']['temperature']
    )
    
    template = """You are a professional AI assistant for document comprehension.
Your task is to answer the user's question BASED STRICTLY ON THE CONTEXT BELOW.
DO NOT use outside knowledge. DO NOT apologize. DO NOT explain that you are a text-based AI.
If the information is not in the context, reply with EXACTLY THIS SENTENCE: "I couldn't find this information in the document."

Context extracted from the document:
{context}

User's question: {input}
Answer:"""

    prompt = ChatPromptTemplate.from_template(template)
    question_answer_chain = create_stuff_documents_chain(global_llm, prompt)
    global_rag_chain = create_retrieval_chain(compression_retriever, question_answer_chain)
    
    print("\nSYSTEM READY WITH GLOBAL KNOWLEDGE BASE!\n" + "="*40)


app = FastAPI(title="PDF - RAG API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class IngestRequest(BaseModel):
    file_path: str
    document_id: int | None = None

@app.post("/api/ingest")
async def ingest_document(request: IngestRequest):
    try:
        ingest_and_rebuild_rag(request.file_path)
        return {"status": "success"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class ChatRequest(BaseModel):
    question: str
    document_id: int | None = None

# --- BACKGROUND EVALUATION FUNCTION (Post-Query Evaluation) ---
def evaluate_single_interaction(question: str, answer: str, contexts: list):
    """Evaluate the quality of each answer immediately after sending to user"""
    print(f"\n[EVALUATION] Running background evaluation for question: {question}")
    
    try:
        metrics = setup_ragas_metrics()
        
        data = {
            "question": [question],
            "answer": [answer],
            "contexts": [[doc.page_content for doc in contexts]]
        }
        dataset = Dataset.from_dict(data)
        
        # Run Ragas (Hide minor system warnings to keep Terminal clean)
        result = evaluate(dataset, metrics=[metrics[0], metrics[1]], raise_exceptions=False) 
        
        print(f"\nQUERY EVALUATION RESULTS (Real-time):")
        
        # Safely extract data from Ragas EvaluationResult
        try:
            faithfulness_score = result["faithfulness"]
        except:
            faithfulness_score = "N/A"
            
        try:
            answer_relevancy_score = result["answer_relevancy"]
        except:
            answer_relevancy_score = "N/A"

        # If Ragas returns a List, take the first element
        if isinstance(faithfulness_score, list) and len(faithfulness_score) > 0:
            faithfulness_score = faithfulness_score[0]
        if isinstance(answer_relevancy_score, list) and len(answer_relevancy_score) > 0:
            answer_relevancy_score = answer_relevancy_score[0]

        print(f"- Faithfulness: {faithfulness_score}")
        print(f"- Answer Relevancy: {answer_relevancy_score}")
        print("="*40)
        
    except Exception as e:
        print(f"\n[EVALUATION SYSTEM ERROR]: Could not complete scoring.")
        print(f"Details: {str(e)}")
        print("="*40)

@app.post("/api/ask")
async def ask_question(request: ChatRequest, background_tasks: BackgroundTasks):
    global global_rag_chain
    if not global_rag_chain:
        raise HTTPException(status_code=404, detail="No documents uploaded.")
    
    try:
        response = global_rag_chain.invoke({"input": request.question})
        contexts = response["context"]
        answer = response["answer"]
        
        sources = [{"page": doc.metadata.get('page', 'N/A'), "snippet": doc.page_content[:150].replace('\n', ' ') + "..."} for doc in contexts]
        
        # Call background Ragas evaluation so user doesn't have to wait
        background_tasks.add_task(evaluate_single_interaction, request.question, answer, contexts)
        
        return {"status": "success", "answer": answer, "sources": sources}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- NEW: SYSTEM BENCHMARK EVALUATION API ---
class BenchmarkRequest(BaseModel):
    csv_path: str # CSV file containing 'question' and 'ground_truth' columns

@app.post("/api/evaluate_system")
async def evaluate_system_benchmark(request: BenchmarkRequest):
    """Run comprehensive pipeline evaluation using a Benchmark CSV file"""
    global global_rag_chain
    if not global_rag_chain:
        raise HTTPException(status_code=400, detail="System has not loaded any documents.")
    
    if not os.path.exists(request.csv_path):
        raise HTTPException(status_code=404, detail="Benchmark CSV file not found.")

    df = pd.read_csv(request.csv_path)
    questions = df['question'].tolist()
    ground_truths = df['ground_truth'].tolist()
    
    answers = []
    contexts = []
    
    print(f"\n[BENCHMARK] Starting system evaluation with {len(questions)} questions...")
    
    # Run AI for each question in the test set
    for q in questions:
        response = global_rag_chain.invoke({"input": q})
        answers.append(response["answer"])
        contexts.append([doc.page_content for doc in response["context"]])
        
    data = {
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truths
    }
    dataset = Dataset.from_dict(data)
    
    # Run Ragas with all 4 metrics
    metrics = setup_ragas_metrics()
    print("[BENCHMARK] Ragas is scoring (may take a few minutes)...")
    result = evaluate(dataset, metrics=metrics)
    
    # Save detailed report to file
    result_df = result.to_pandas()
    result_df.to_csv("benchmark_report.csv", index=False)
    
    return {
        "status": "success",
        "global_score": result,
        "message": "Detailed report saved to benchmark_report.csv"
    }

if __name__ == "__main__":
    print("Starting API server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)