import os
from dotenv import load_dotenv
import redis

load_dotenv()

def get_redis_client():
    REDIS_HOST = os.getenv("REDIS_HOST")
    REDIS_PORT = os.getenv("REDIS_PORT")
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
    
    # Clean host if needed
    if REDIS_HOST and ':' in REDIS_HOST:
        REDIS_HOST = REDIS_HOST.split(':')[0]
    
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=int(REDIS_PORT),
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5
    )
    return redis_client