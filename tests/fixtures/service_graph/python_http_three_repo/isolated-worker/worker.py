from flask import Flask

app = Flask(__name__)


@app.get("/catalog")
def catalog():
    return []


def helper(value: str) -> str:
    return value.upper()
