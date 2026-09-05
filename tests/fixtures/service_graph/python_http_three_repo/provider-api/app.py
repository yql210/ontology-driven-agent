from fastapi import APIRouter, FastAPI
from flask import Flask

app = FastAPI()
router = APIRouter(prefix="/v1")
flask_app = Flask(__name__)
METHODS = ("GET",)


@app.get("/orders")
def list_orders():
    return []


@app.post("/orders")
async def create_order():
    return {}


@router.get("/orders/{order_id}")
def get_order(order_id: str):
    return {"id": order_id}


@flask_app.route("/flask/orders", methods=["GET", "POST"])
def flask_orders():
    return "ok"


@app.route("/unsupported", methods=METHODS)
def unsupported_route():
    return {}
