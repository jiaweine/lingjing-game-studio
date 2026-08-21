.PHONY: run run-backend worker migrate test benchmark ui-e2e e2e train prod-up prod-down

run: run-backend

run-backend:
	uvicorn worldforge.api.app:app --host 0.0.0.0 --port 8765 --reload

worker:
	python -m worldforge.worker

migrate:
	alembic upgrade head

train:
	python scripts/train_policy.py --seeds 16 --epochs 520

test:
	python -m pytest
	node --check frontend/app.js
	python -m compileall -q worldforge domains migrations scripts tests

benchmark:
	python -m worldforge.cli benchmark --seeds 12

ui-e2e:
	python scripts/product_ui_e2e.py

e2e:
	python scripts/product_backend_e2e.py

prod-up:
	docker compose -f docker-compose.prod.yml up --build

prod-down:
	docker compose -f docker-compose.prod.yml down
