from langchain_core.globals import set_verbose, set_debug
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain.schema.output_parser import StrOutputParser
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema.runnable import RunnablePassthrough
from langchain_community.vectorstores.utils import filter_complex_metadata
from langchain_core.prompts import ChatPromptTemplate
import logging
import requests
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

set_debug(True)
set_verbose(True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ChatPDFProcessor:
    """
    Class for processing PDF documents and enabling question-answering using an LLM model.
    """
    #to test smaller model change next line to mistral:latest instead of deepseek
    def __init__(self, llm_model: str = 'deepseek-coder:latest', embedding_model: str = 'mxbai-embed-large', tavily_api_key: str = None):
        """
        Initializes the ChatPDFProcessor with the provided LLM and embedding models.

        Args:
        - llm_model (str): LLM model for question answering.
        - embedding_model (str): Embedding model for document processing.
        - tavily_api_key (str): Tavily API key for fallback when no context is found.
        """
        self.llm_model = ChatOllama(model=llm_model)
        self.embedding_model = OllamaEmbeddings(model=embedding_model)
        self.text_splitter = RecursiveCharacterTextSplitter(chunk_size=1024, chunk_overlap=100)
        self.prompt_template = ChatPromptTemplate.from_template(
            """
            You are a helpful assistant answering questions based on the uploaded document.
            Context:
            {context}
            
            Question:
            {question}
            
            Provide a clear and helpful explanation.
            """
        )
        self.vector_store = None
        self.retriever = None
        self.tavily_api_key = tavily_api_key

    def ingest_pdf(self, pdf_file_path: str):
        """
        Ingests a PDF document, processes it into embeddings, and stores it in a vector store.

        Args:
        - pdf_file_path (str): Path to the PDF document to ingest.
        """
        logger.info(f"Starting ingestion for file: {pdf_file_path}")
        documents = PyPDFLoader(file_path=pdf_file_path).load()
        chunks = self.text_splitter.split_documents(documents)
        chunks = filter_complex_metadata(chunks)

        self.vector_store = Chroma.from_documents(
            documents=chunks,
            embedding=self.embedding_model,
            persist_directory="chroma_db",
        )
        logger.info("Ingestion completed. Document embeddings stored successfully.")

    def query_document(self, query: str, k: int = 5, score_threshold: float = 0.2):
        """
        Queries the ingested document and retrieves relevant context using the vector store.

        Args:
        - query (str): The question to ask.
        - k (int): The number of relevant documents to retrieve (default is 5).
        - score_threshold (float): The minimum similarity score to consider for retrieved documents (default is 0.2).

        Returns:
        - str: The LLM-generated answer or a message if no relevant context is found.
        """
        if not self.vector_store:
            raise ValueError("No document ingested. Please ingest a document first.")
            
        if not self.retriever:
            self.retriever = self.vector_store.as_retriever(
                search_type='similarity_score_threshold',
                search_kwargs={"k": k, "score_threshold": score_threshold},
            )
        
        logger.info(f"Retrieving context for query: {query}")
        retrieved_docs = self.retriever.invoke(query)
        if not retrieved_docs:
            # If no relevant context found, fallback to Tavily
            logger.info("No relevant context found in PDF. Using Tavily for fallback.")
            return self.query_tavily(query)
        
        formatted_input = {
            "context": "\n\n".join(doc.page_content for doc in retrieved_docs),
            "question": query,
        }

        chain = (
            RunnablePassthrough() 
            | self.prompt_template 
            | self.llm_model 
            | StrOutputParser()
        )
        logger.info("Generating response using the LLM.") 
        return chain.invoke(formatted_input)

    def query_tavily(self, query: str):
        """
        Queries Tavily API for context when no relevant information is found in the PDF.

        Args:
        - query (str): The question to ask.

        Returns:
        - str: The response from Tavily.
        """
        headers = {"Authorization": f"Bearer {self.tavily_api_key}"}
        response = requests.post(
            "https://api.tavily.com/v1/query", json={"query": query}, headers=headers
        )
        if response.status_code == 200:
            return response.json().get("answer", "No answer found in Tavily.")
        else:
            return "Tavily API error. Please try again later."

    def clear_data(self):
        """
        Clears the vector store and retriever to reset the system.
        """
        logger.info("Clearing vector store and retriever.")
        self.vector_store = None
        self.retriever = None
