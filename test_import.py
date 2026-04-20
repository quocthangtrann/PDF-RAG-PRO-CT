import sys
import traceback
try:
    from langchain_community.embeddings import HuggingFaceEmbeddings
    print("HuggingFaceEmbeddings imported successfully")
except Exception as e:
    traceback.print_exc()

try:
    from ragas.metrics import Faithfulness, AnswerRelevancy
    print("Ragas imported successfully")
except Exception as e:
    traceback.print_exc()

