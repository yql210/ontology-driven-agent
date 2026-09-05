import httpx
import requests

mystery = object()


def checkout():
    requests.get("http://provider.internal/orders")
    requests.post("http://provider.internal/orders")
    requests.request("GET", "http://provider.internal/v1/orders/{order_id}")
    httpx.Client().get("http://provider.internal/flask/orders")


async def async_checkout():
    async with httpx.AsyncClient() as client:
        await client.post("http://provider.internal/flask/orders")
    await httpx.AsyncClient().request("POST", "http://provider.internal/orders")


def dynamic_target(order_id: str):
    requests.get(f"http://provider.internal/orders/{order_id}")
    mystery.get("http://provider.internal/orders")


def helper(value: str) -> str:
    return value.upper()


requests.get("http://provider.internal/orders")
