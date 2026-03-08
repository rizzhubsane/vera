import json
from groq import Groq
from agent.tools.database import get_reviews

def query_reviews(question: str) -> str:
    """Takes natural language question, returns grounded answer using a two-pass reasoning pipeline."""
    
    client = Groq()
    
    # --- PASS 1: Intent Extraction ---
    intent_system_prompt = (
        "You are a query parser. Extract structured filters from the user's question. "
        "Return ONLY valid JSON with these optional keys:\n"
        "{\n"
        "  product_id: 'product_a' | 'product_b' | null,\n"
        "  sentiment: 'Positive' | 'Negative' | 'Neutral' | null,\n"
        "  theme: one of [Sound Quality, Battery Life, Comfort & Fit, "
        "App Experience, Price & Value, Delivery, Build Quality, ANC] | null,\n"
        "  keyword: string | null,\n"
        "  limit: number (default 30)\n"
        "}"
    )
    
    intent_response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": intent_system_prompt},
            {"role": "user", "content": f"Question: {question}"}
        ],
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    
    try:
        filters = json.loads(intent_response.choices[0].message.content)
    except Exception:
        filters = {"limit": 30}
    
    # --- EXECUTE QUERY ---
    # get_reviews accepts product_id, sentiment, theme, limit
    # We filter out keywords manually if provided since get_reviews doesn't directly take it as a keyword arg (it has search_reviews_by_keyword but let's stick to get_reviews for now or integrate)
    # Actually, get_reviews in database.py DOES NOT take keyword. 
    # Let's adjust filters for get_reviews.
    
    # Map filters to get_reviews parameters
    query_params = {
        "product_id": filters.get("product_id"),
        "sentiment": filters.get("sentiment"),
        "theme": filters.get("theme"),
        "limit": filters.get("limit") or 30
    }
    
    # Filter out None values
    query_params = {k: v for k, v in query_params.items() if v is not None}
    
    reviews = get_reviews(**query_params)
    
    # Handle keyword filtering in memory ifPass 1 extracted a keyword
    keyword = filters.get("keyword")
    if keyword and reviews:
        reviews = [r for r in reviews if keyword.lower() in (r.get("review_text", "").lower() + r.get("review_title", "").lower())]

    # --- PASS 2: Synthesis ---
    synthesis_system_prompt = (
        "You are Vera, a Voice of Customer analyst. Answer the user's question using ONLY the reviews provided. You must:\n"
        "1. State your conclusion first, then support it with evidence\n"
        "2. Cite exact review counts (e.g. '8 of 12 negative reviews mention...')\n"
        "3. Quote 2-3 reviews verbatim (keep quotes under 20 words)\n"
        "4. Compare products only when both have data\n"
        "5. If data is insufficient, say exactly what's missing\n"
        "Never speculate beyond the provided reviews."
    )
    
    user_message = (
        f"Question: {question}\n\n"
        f"Review data ({len(reviews)} reviews):\n"
        f"{json.dumps(reviews, indent=2)}"
    )
    
    synthesis_response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": synthesis_system_prompt},
            {"role": "user", "content": user_message}
        ],
        temperature=0.2
    )
    
    return synthesis_response.choices[0].message.content
