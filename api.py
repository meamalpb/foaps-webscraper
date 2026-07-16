import json
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

PRODUCTS_JSON = Path("results/products.json")

app = FastAPI(title="FOAPS Flyer Products API")


class Product(BaseModel):
    name: str
    description: str
    original_price: float | None
    discounted_price: float | None
    merchant_name: str


@app.get("/products", response_model=list[Product])
def get_products():
    with open(PRODUCTS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    products = []
    for merchant_name, merchant_data in data.items():
        for page in merchant_data.get("pages", []):
            for product in page.get("products", []):
                products.append(
                    Product(merchant_name=merchant_name, **product)
                )

    return products
