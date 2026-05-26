# Makefile — Cloud Cost Anomaly Agent
#
# Targets:
#   make build          build for the current machine's native platform (fast)
#   make build-amd64    build for Linux x86_64 only (Lambda default)
#   make build-arm64    build for Linux arm64 only  (Graviton / Apple Silicon)
#   make build-multi    build for both platforms and push to ECR
#   make ecr-login      authenticate Docker to ECR
#   make ecr-push       login + build-multi in one step
#   make run-local      run the agent locally against real AWS/ES using .env.local
#   make test           run the integration test suite
#   make clean          remove build artefacts

# ---------------------------------------------------------------------------
# Config — override on the command line: make ecr-push REGION=eu-west-1
# ---------------------------------------------------------------------------
REGION       ?= us-east-1
LAMBDA_ARCH  ?= x86_64          # x86_64 | arm64 — must match your Lambda setting
TAG          ?= latest
IMAGE_NAME   := cost-anomaly-agent

# Resolve account ID at runtime (requires aws CLI + valid credentials)
ACCOUNT_ID   := $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
ECR_REPO     := $(ACCOUNT_ID).dkr.ecr.$(REGION).amazonaws.com/$(IMAGE_NAME)

# Map Lambda arch names to Docker platform strings
ifeq ($(LAMBDA_ARCH),arm64)
  LAMBDA_PLATFORM := linux/arm64
else
  LAMBDA_PLATFORM := linux/amd64
endif

# ---------------------------------------------------------------------------
# Builder setup
# BuildKit is required for multi-platform builds and --platform=$BUILDPLATFORM.
# The 'multi' builder uses the docker-container driver which supports both
# linux/amd64 and linux/arm64 on any host.
# ---------------------------------------------------------------------------
BUILDER := cost-agent-builder

.PHONY: builder-init
builder-init:
	@if ! docker buildx inspect $(BUILDER) > /dev/null 2>&1; then \
	  echo "Creating buildx builder '$(BUILDER)' ..."; \
	  docker buildx create \
	    --name $(BUILDER) \
	    --driver docker-container \
	    --platform linux/amd64,linux/arm64 \
	    --use; \
	else \
	  docker buildx use $(BUILDER); \
	fi

# ---------------------------------------------------------------------------
# Build targets
# ---------------------------------------------------------------------------

## Build for the host's native platform (fastest — no emulation)
.PHONY: build
build:
	DOCKER_BUILDKIT=1 docker build \
	  --tag $(IMAGE_NAME):$(TAG) \
	  --tag $(IMAGE_NAME):latest \
	  .
	@echo "✅ Built $(IMAGE_NAME):$(TAG) for native platform"

## Build for linux/amd64 (x86_64 Lambda) — works on any host
.PHONY: build-amd64
build-amd64: builder-init
	docker buildx build \
	  --builder $(BUILDER) \
	  --platform linux/amd64 \
	  --tag $(IMAGE_NAME):$(TAG)-amd64 \
	  --load \
	  .
	@echo "✅ Built $(IMAGE_NAME):$(TAG)-amd64"

## Build for linux/arm64 (Graviton Lambda / Apple Silicon) — works on any host
.PHONY: build-arm64
build-arm64: builder-init
	docker buildx build \
	  --builder $(BUILDER) \
	  --platform linux/arm64 \
	  --tag $(IMAGE_NAME):$(TAG)-arm64 \
	  --load \
	  .
	@echo "✅ Built $(IMAGE_NAME):$(TAG)-arm64"

## Build and push a multi-arch manifest to ECR (both amd64 + arm64)
.PHONY: build-multi
build-multi: builder-init
	@test -n "$(ACCOUNT_ID)" || (echo "❌ Could not resolve AWS account ID. Run 'aws sso login' first." && exit 1)
	docker buildx build \
	  --builder $(BUILDER) \
	  --platform linux/amd64,linux/arm64 \
	  --tag $(ECR_REPO):$(TAG) \
	  --tag $(ECR_REPO):latest \
	  --push \
	  .
	@echo "✅ Multi-arch image pushed: $(ECR_REPO):$(TAG)"
	@echo "   Covers: linux/amd64 and linux/arm64"

# ---------------------------------------------------------------------------
# ECR targets
# ---------------------------------------------------------------------------

## Authenticate Docker to ECR (required before pushing)
.PHONY: ecr-login
ecr-login:
	@test -n "$(ACCOUNT_ID)" || (echo "❌ Could not resolve AWS account ID." && exit 1)
	aws ecr get-login-password --region $(REGION) \
	  | docker login --username AWS --password-stdin \
	    $(ACCOUNT_ID).dkr.ecr.$(REGION).amazonaws.com
	@echo "✅ Authenticated to ECR ($(REGION))"

## Create the ECR repository if it doesn't exist
.PHONY: ecr-create-repo
ecr-create-repo:
	@aws ecr describe-repositories --repository-names $(IMAGE_NAME) \
	    --region $(REGION) > /dev/null 2>&1 \
	  || aws ecr create-repository \
	       --repository-name $(IMAGE_NAME) \
	       --region $(REGION) \
	       --image-scanning-configuration scanOnPush=true \
	       --image-tag-mutability MUTABLE
	@echo "✅ ECR repository ready: $(ECR_REPO)"

## Full push workflow: login → create repo (idempotent) → multi-arch build + push
.PHONY: ecr-push
ecr-push: ecr-login ecr-create-repo build-multi
	@echo "✅ ecr-push complete"
	@echo "   Update Lambda to use: $(ECR_REPO):$(TAG)"

## Update the Lambda function to use the new ECR image
.PHONY: lambda-update-image
lambda-update-image:
	aws lambda update-function-code \
	  --function-name $(IMAGE_NAME) \
	  --image-uri $(ECR_REPO):$(TAG) \
	  --architectures $(LAMBDA_ARCH) \
	  --region $(REGION)
	@echo "✅ Lambda function updated to $(ECR_REPO):$(TAG) [$(LAMBDA_ARCH)]"

# ---------------------------------------------------------------------------
# Local run target
# ---------------------------------------------------------------------------
# Requires .env.local with real credentials — never commit this file.
# The agent hits real AWS Bedrock and real Elasticsearch.
#
# .env.local format:
#   ELASTIC_SECRET_ARN=arn:aws:secretsmanager:...
#   SLACK_SECRET_ARN=arn:aws:secretsmanager:...
#   AWS_ACCESS_KEY_ID=...
#   AWS_SECRET_ACCESS_KEY=...
#   AWS_SESSION_TOKEN=...         # if using SSO / assumed role
#   AWS_BEDROCK_REGION=us-east-1
#   SPIKE_THRESHOLD_PCT=25.0
#   AGENT_MAX_ITERATIONS=20

.PHONY: run-local
run-local: build
	@test -f .env.local || (echo "❌ .env.local not found. See Makefile comments for format." && exit 1)
	docker run --rm \
	  --env-file .env.local \
	  --platform linux/amd64 \
	  -p 9000:8080 \
	  $(IMAGE_NAME):$(TAG) &
	@echo "⏳ Waiting for Lambda runtime to start..."
	@sleep 2
	curl -s -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" \
	  -d '{"source":"local-test"}' | python3 -m json.tool
	@pkill -f "docker run.*cost-anomaly-agent" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Test targets
# ---------------------------------------------------------------------------

## Run the integration test suite (mocked — no real AWS calls)
.PHONY: test
test:
	python3 -m pytest tests/test_integration.py -v

## Run tests with coverage report
.PHONY: test-coverage
test-coverage:
	python3 -m pytest tests/test_integration.py -v \
	  --cov=agent --cov=tools \
	  --cov-report=term-missing

# ---------------------------------------------------------------------------
# Build the ZIP deployment package (alternative to container image)
# ---------------------------------------------------------------------------
.PHONY: zip
zip:
	rm -rf package/ $(IMAGE_NAME).zip
	pip install -r requirements.txt -t package/ --quiet
	cp agent.py package/
	cp -r tools/ package/tools/
	cd package && zip -r ../$(IMAGE_NAME).zip . -x "*.pyc" -x "*/__pycache__/*"
	ls -lh $(IMAGE_NAME).zip
	@echo "✅ $(IMAGE_NAME).zip ready for Lambda ZIP upload"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
.PHONY: clean
clean:
	rm -rf package/ $(IMAGE_NAME).zip .pytest_cache/ __pycache__/ tools/__pycache__/
	@echo "✅ Clean"
