-include .env
export

IMAGE     = auto-downloader
REGISTRY  = $(DOCKER_USER)/$(IMAGE)
TAG       = latest

.PHONY: build test push release dev dev-seed dev-down dev-reset

build:
	docker compose -f app/docker-compose.yml build

test: build
	docker compose -f app/docker-compose.yml run --rm ytmusic pytest tests -v

push: test
	docker build --no-cache -t $(IMAGE):$(TAG) ./app
	docker tag $(IMAGE):$(TAG) $(REGISTRY):$(TAG)
	docker push $(REGISTRY):$(TAG)

release: push

# ── Local sandbox (http://localhost:8081, data in ./dev, never the NAS) ──────
DEV_COMPOSE = docker compose -f app/docker-compose.dev.yml

dev: build
	mkdir -p dev/data dev/downloads dev/music
	$(DEV_COMPOSE) up -d --build
	@echo "Player: http://localhost:8081   (make dev-seed adds sample tracks, make dev-down stops)"

dev-seed:
	mkdir -p dev/data dev/downloads dev/music
	$(DEV_COMPOSE) run --rm --no-deps -v "$(CURDIR)/dev-seed.sh:/seed.sh:ro" --entrypoint sh ytmusic /seed.sh
	-curl -s -X POST http://localhost:8081/api/library/rescan >/dev/null

dev-down:
	$(DEV_COMPOSE) down

dev-reset: dev-down
	rm -rf dev
