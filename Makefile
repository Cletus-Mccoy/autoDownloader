-include .env
export

IMAGE     = auto-downloader
REGISTRY  = $(DOCKER_USER)/$(IMAGE)
TAG       = latest

.PHONY: build test push release

build:
	docker compose -f app/docker-compose.yml build

test: build
	docker compose -f app/docker-compose.yml run --rm ytmusic pytest tests -v

push: test
	docker login
	docker build --no-cache -t $(IMAGE):$(TAG) ./app
	docker tag $(IMAGE):$(TAG) $(REGISTRY):$(TAG)
	docker push $(REGISTRY):$(TAG)

release: push
