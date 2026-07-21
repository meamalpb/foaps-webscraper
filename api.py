import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

PRODUCTS_JSON = Path("results/products.json")
EXPIRES_BY_FORMAT = "%d-%m-%Y"

app = FastAPI(title="FOAPS Flyer Products API")


class Product(BaseModel):
    name: str
    description: str
    original_price: float | None
    discounted_price: float | None
    merchant_name: str
    city: str
    expires_by: str | None


def is_expired(expires_by):
    if not expires_by:
        return False
    try:
        expiry_date = datetime.strptime(expires_by, EXPIRES_BY_FORMAT).date()
    except ValueError:
        return False
    return expiry_date < datetime.now().date()


@app.get("/products", response_model=list[Product])
def get_products():
    with open(PRODUCTS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    products = []
    for city in data.get("cities", []):
        city_name = city["city"]
        for retailer in city.get("retailers", []):
            merchant_name = retailer["name"]
            for flyer in retailer.get("flyers", []):
                expires_by = flyer.get("expires_by")
                if is_expired(expires_by):
                    continue
                for page in flyer.get("pages", []):
                    for product in page.get("products", []):
                        products.append(
                            Product(
                                merchant_name=merchant_name,
                                city=city_name,
                                expires_by=expires_by,
                                **product,
                            )
                        )

    return products
