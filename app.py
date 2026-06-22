from fastapi import FastAPI
from src.routes import whatsapp
from src.routes import webhook_routes

app = FastAPI()


# Register routes
app.include_router(whatsapp.router)
app.include_router(webhook_routes.router)

@app.get("/")   
def root():
    return {"message": "WhatsApp Chatbot Running 🚀"}