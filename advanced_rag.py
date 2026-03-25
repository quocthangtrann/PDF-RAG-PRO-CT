import os
import yaml
from dotenv import load_dotenv

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

def build_advanced_rag():
    pdf_path = config['files']['document_path']
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
    print(f"-> Document split into {len(chunks)} chunks.")

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
        temperature=config['model']['temperature'],
        convert_system_message_to_human=True 
    )
    
    system_prompt = (
        "You are a professional AI assistant who understands documents. "
        "Use the context below to answer the question. "
        "If you don't find the information, say 'I couldn't find this information in the document', don't make things up.\n\n"
        "Document context:\n{context}"
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
    ])
    
    question_answer_chain = create_stuff_documents_chain(llm, prompt)
    rag_chain = create_retrieval_chain(compression_retriever, question_answer_chain)
    
    print("\nSYSTEM READY!\n" + "="*40)
    return rag_chain

def chat_interface():
    try:
        rag_pipeline = build_advanced_rag()
    except Exception as e:
        print(f"Error initializing system: {e}")
        return
    
    while True:
        user_query = input("\n👤 You: ")
        if user_query.lower() in ['exit', 'quit', 'thoát', 'q']:
            print("🤖 Chatbot: goodbye!")
            break
            
        print("🤖 Chatbot thinking...")
        try:
            response = rag_pipeline.invoke({"input": user_query})
            print("\n🤖 answer:", response["answer"])
            
            print("\nReference:")
            for i, doc in enumerate(response["context"]):
                snippet = doc.page_content[:100].replace('\n', ' ')
                print(f"  [{i+1}] Trang {doc.metadata.get('page', 'N/A')}: {snippet}...")
        except Exception as e:
            print(f"\nerror: {e}")

if __name__ == "__main__":
    chat_interface()