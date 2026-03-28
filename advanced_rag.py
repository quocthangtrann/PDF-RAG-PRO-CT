import os
import yaml
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Document Loaders & Splitters
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter

# LLM & Embeddings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.embeddings import HuggingFaceEmbeddings

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

# config and download env var
load_dotenv()
with open("config.yaml", "r", encoding="utf-8") as file:
    config = yaml.safe_load(file)

def build_advanced_rag(pdf_path: str):
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Do not find: {pdf_path}")

    print("[1/5] Reading PDF...")
    loader = PyPDFLoader(pdf_path)
    docs = loader.load()

    print("[2/5] Splitting text (Recursive Character Splitter)...")
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    
    # cut the text into smaller chunks with some overlap, using a hierarchy of separators to preserve context
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n\n", "\n", ".", " "]
    )
    chunks = text_splitter.split_documents(docs)

    print("[3/5] Setting up Hybrid Search (ChromaDB + BM25)...")
    vectorstore = Chroma.from_documents(chunks, embeddings)
    vector_retriever = vectorstore.as_retriever(
        search_kwargs={"k": config['retrieval']['vector_search_k']}
    )

    bm25_retriever = BM25Retriever.from_documents(chunks)
    bm25_retriever.k = config['retrieval']['bm25_search_k']

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, vector_retriever], 
        weights=config['retrieval']['ensemble_weights']
    )

    print("[4/5] Setting up Cohere Re-ranker...")
    compressor = CohereRerank(top_n=config['retrieval']['rerank_top_n'])
    compression_retriever = ContextualCompressionRetriever(
        base_compressor=compressor, 
        base_retriever=ensemble_retriever
    )

    print("[5/5] Setting up Google Gemini LLM...")
    llm = ChatGoogleGenerativeAI(
        model=config['model']['llm_name'],
        temperature=config['model']['temperature']
    )
    
    # Combine the Prompt into a single, strictly enforced instruction
    template = """You are a professional AI assistant for document comprehension.
Your task is to answer the user's question BASED STRICTLY ON THE CONTEXT BELOW.
DO NOT use outside knowledge. DO NOT apologize. DO NOT explain that you are a text-based AI.
If the information is not in the context, reply with EXACTLY THIS SENTENCE: "I couldn't find this information in the document."

Context extracted from the document:
{context}

User's question: {input}
Answer:"""

    prompt = ChatPromptTemplate.from_template(template)
    
    question_answer_chain = create_stuff_documents_chain(llm, prompt)
    rag_chain = create_retrieval_chain(compression_retriever, question_answer_chain)
    
    print("\nSYSTEM READY!\n" + "="*40)
    return rag_chain

app = FastAPI(title="PDF - RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# save rag pipeline in global var to reuse across requests 
rag_pipelines = {}

# new api
class IngestRequest(BaseModel):
    document_id: int
    file_path: str

@app.post("/api/ingest")
async def ingest_document(request: IngestRequest):
    try:
        # build RAG pipeline for doc, save it in global dict with document_id as key
        chain = build_advanced_rag(request.file_path)
        rag_pipelines[request.document_id] = chain
        print(f"upload doc_id successfully {request.document_id} into AI mem")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ChatRequest(BaseModel):
    document_id: int
    question: str

@app.post("/api/ask")
async def ask_question(request: ChatRequest):
    # retrieve the corresponding RAG pipeline for the doc_id, if not found return error
    chain = rag_pipelines.get(request.document_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Document not found. Please upload the document first.")
    
    try:
        response = chain.invoke({"input": request.question})
        sources = [{"page": doc.metadata.get('page', 'N/A'), "snippet": doc.page_content[:150].replace('\n', ' ') + "..."} for doc in response["context"]]
        return {"status": "success", "answer": response["answer"], "sources": sources}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("Starting API server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)