echo ""
echo "===== Running main.py ====="
uv run python main.py

echo ""
echo "===== Running fetch_flyers.py ====="
uv run python fetch_flyers.py

echo ""
echo "===== Running extract_images.py ====="
uv run python extract_images.py

echo ""
echo "===== Running extract_products.py ====="
uv run python extract_products.py

echo ""
echo "========================================"
echo "All steps completed successfully!"
echo "Results are available in the results/ directory."
echo "========================================"