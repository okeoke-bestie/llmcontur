.PHONY: install run test eval data analyze simulate
install:  ; pip install -r requirements-dev.txt
run:      ; uvicorn app.main:app --reload
test:     ; pytest -q
eval:     ; python -m eval.run_eval
data:     ; python -m scripts.generate_data
analyze:  ; python -m scripts.analyze_history
simulate: ; python -m scripts.simulate --n 500
