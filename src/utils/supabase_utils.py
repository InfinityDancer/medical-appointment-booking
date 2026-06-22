import io
import json
import os
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv
from supabase import create_client, Client
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from docx import Document

load_dotenv()

# Initialize Supabase client
url = os.environ.get("SUPABASE_URL", "")
key = os.environ.get("SUPABASE_KEY", "")

# only create the client if the URL and KEY are present
supabase: Client | None = create_client(url, key) if url and key else None

def get_organisation_id(organisation_name: str) -> str | None:
    """
    Queries the organisation_details table in Supabase to find the
    organisation_id for the given clinic name.
    Returns the organisation_id string, or None if not found.
    """
    if not supabase:
        print("Supabase client not initialized. Check credentials.")
        return None

    try:
        response = (
            supabase.table("organisation_details")
            .select("organisation_id")
            .eq("organisation_name", organisation_name)
            .limit(1)
            .execute()
        )

        if response.data and len(response.data) > 0:
            org_id = response.data[0].get("organisation_id")
            print(f"Found organisation_id: {org_id} for clinic: {organisation_name}")
            return org_id
        else:
            print(f"No organisation found for clinic: {organisation_name}")
            return None

    except Exception as e:
        print(f"Error fetching organisation_id: {e}")
        return None


def check_bucket_exists(bucket_name: str) -> bool:
    """
     Checks if a bucket exists and prints a message.
    """
    if not supabase:
        print("Supabase client not initialized. Check credentials.")
        return False

    try:
        buckets = supabase.storage.list_buckets()
        for b in buckets:
            if b.name == bucket_name:
                print(f"Bucket '{bucket_name}' exists.")
                return True

        print(f"Bucket '{bucket_name}' does not exist. Please create it manually.")
        return False
    except Exception as e:
        print(f"Error checking bucket: {e}")
        return False

def _extract_text_from_pdf(content_bytes: bytes) -> str:
    """Extracts text from PDF file bytes."""
    reader = PdfReader(io.BytesIO(content_bytes))
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += (f"{page_text}\n")
    return text


def _extract_text_from_docx(content_bytes: bytes) -> str:
    """Extracts text from DOCX file bytes."""
    doc = Document(io.BytesIO(content_bytes))
    return "\n".join(para.text for para in doc.paragraphs if para.text)


def _detect_file_type(link: str) -> str:
    """Detects file type from the URL extension."""
    path = urlparse(link).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    elif path.endswith(".docx"):
        return "docx"
    return "text"


def generate_embeddings(link: str, chunk_size: int = 600, chunk_overlap: int = 200) -> list[dict] | None:
    """
    Downloads the document from the generated link, extracts text,
    splits it into chunks, and generates embeddings using OpenAIEmbeddings.
    Supports PDF, DOCX, and plain text files.

    Returns a list of dicts, each containing:
        - chunk_id (int): 0-indexed position of the chunk
        - content (str): the text content of the chunk
        - metadata (dict): file_type, chunk_index, total_chunks, source
        - embedding (list[float]): the embedding vector
    """
    try:
        print(f"Downloading content from: {link}")
        # 1. Download the document from the link
        response = requests.get(link)
        response.raise_for_status()

        # 2. Detect file type and extract text accordingly
        file_type = _detect_file_type(link)
        if file_type == "pdf":
            print("Detected PDF file, extracting text...")
            text_content = _extract_text_from_pdf(response.content)
        elif file_type == "docx":
            print("Detected DOCX file, extracting text...")
            text_content = _extract_text_from_docx(response.content)
        else:
            print("Treating as plain text file...")
            text_content = response.text

        if not text_content.strip():
            print("Warning: No text content extracted from the document.")
            return None

        # 3. Split the document
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        chunks = text_splitter.split_text(text_content)
        print(f"Split document into {len(chunks)} chunks.")

        # 4. Generate embeddings
        print(f"Generating embeddings for {len(chunks)} chunks...")
        embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")
        embeddings = embeddings_model.embed_documents(chunks)

        print(f"Successfully generated {len(embeddings)} embeddings of dimension {len(embeddings[0]) if embeddings else 0}")

        # 5. Build structured result
        result = []
        for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            result.append({
                "chunk_id": i,
                "content": chunk_text,
                "metadata": {
                    "file_type": file_type,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "source": link,
                },
                "embedding": embedding,
            })

        return result

    except Exception as e:
        print(f"Error generating embeddings: {e}")
        return None

def process_single_row(
    table_name: str,
    row_id: str,
    org_id: str,
    doc_link: str,
    pk_column: str = "organisation_services_id",
) -> None:
    """
    Processes a single row (called from the webhook endpoint).
    Downloads the document, chunks it, generates embeddings,
    deletes the original stub row, and inserts N chunked rows.
    """
    if not supabase:
        print("Supabase client not initialized. Check credentials.")
        return

    try:
        print(f"\n{'='*60}")
        print(f"⚙️  [PROCESS] Starting embedding generation for row {row_id}")
        print(f"⚙️  [PROCESS] Table: {table_name}")
        print(f"⚙️  [PROCESS] Document: {doc_link}")
        print(f"{'='*60}")

        # 1. Generate chunks + embeddings
        chunk_data = generate_embeddings(doc_link)

        if not chunk_data:
            print(f"❌ [PROCESS] Failed to generate chunks/embeddings for row {row_id}")
            return

        print(f"✅ [PROCESS] Generated {len(chunk_data)} chunks")

        # 2. Find max existing chunk_id for this org
        chunk_id_offset = 0
        max_chunk_res = (
            supabase.table(table_name)
            .select("chunk_id")
            .eq("organisation_id", org_id)
            .not_.is_("chunk_id", "null")
            .order("chunk_id", desc=True)
            .limit(1)
            .execute()
        )
        if max_chunk_res.data:
            chunk_id_offset = max_chunk_res.data[0]["chunk_id"] + 1
            print(f"📊 [PROCESS] Existing max chunk_id for org {org_id} is {chunk_id_offset - 1}. "
                  f"New chunks start at {chunk_id_offset}.")

        # 3. Delete the original stub row
        supabase.table(table_name).delete().eq(pk_column, row_id).execute()
        print(f"🗑️  [PROCESS] Deleted original stub row {row_id}")

        # 4. Insert N chunked rows
        new_rows = []
        for chunk in chunk_data:
            new_row = {
                "organisation_id": org_id,
                "related_document": doc_link,
                "chunk_id": chunk["chunk_id"] + chunk_id_offset,
                "content": chunk["content"],
                "metadata": json.dumps(chunk["metadata"]),
                "vector_embeddings": chunk["embedding"],
            }
            new_rows.append(new_row)

        insert_res = supabase.table(table_name).insert(new_rows).execute()

        if insert_res.data:
            print(f"✅ [PROCESS] Inserted {len(insert_res.data)} chunk rows into {table_name}")
        else:
            print(f"❌ [PROCESS] Failed to insert chunk rows for row {row_id}")

    except Exception as e:
        print(f"❌ [PROCESS] Error processing row {row_id}: {e}")


def semantic_search(
    query: str,
    organisation_id: str,
    match_threshold: float = 0.5,
    match_count: int = 5,
) -> list[dict] | None:
    """
    Accepts a user query, generates its vector embedding, and performs
    semantic search against organisation_services_3 via the
    match_organisation_services_3 RPC.
    """
    if not supabase:
        print("Supabase client not initialized. Check credentials.")
        return None

    try:
        # 1. Generate query embedding
        embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")
        query_embedding = embeddings_model.embed_query(query)

        # 2. Call the Supabase RPC for vector similarity search
        response = supabase.rpc(
            "match_organisation_services_3",
            {
                "query_embedding": query_embedding,
                "filter_org_id": organisation_id,
                "match_threshold": match_threshold,
                "match_count": match_count,
            },
        ).execute()

        if response.data:
            print(f"Semantic search returned {len(response.data)} results.")
            return response.data
        else:
            print("Semantic search returned no results.")
            return []

    except Exception as e:
        print(f"Error during semantic search: {e}")
        return None


if __name__ == "__main__":
    # Fetch all organisations and let the user pick one
    orgs_response = supabase.table("organisation_details").select("organisation_id, organisation_name").execute()
    orgs = orgs_response.data if orgs_response.data else []

    if not orgs:
        print("No organisations found in the database.")
        exit(1)

    print("Available Organisations:")
    for idx, org in enumerate(orgs, 1):
        print(f"  {idx}. {org['organisation_name']} ({org['organisation_id']})")

    choice = input(f"\nSelect organisation (1-{len(orgs)}): ").strip()
    try:
        selected = orgs[int(choice) - 1]
    except (ValueError, IndexError):
        print("Invalid selection.")
        exit(1)

    ORG_ID = selected["organisation_id"]
    print(f"\nSemantic Search — {selected['organisation_name']}")
    print(f"Organisation ID: {ORG_ID}")
    print("Type 'quit' to exit.\n")

    while True:
        query = input("Enter your search query: ").strip()
        if query.lower() in ("quit", "exit", "q"):
            print("Exiting.")
            break
        if not query:
            continue

        results = semantic_search(query, ORG_ID)

        if not results:
            print("No results found.\n")
            continue

        print(f"\nFound {len(results)} result(s):\n")
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r['similarity']:.3f}]")
            print(f"{r['content']}")
            print("-" * 50)
            print()