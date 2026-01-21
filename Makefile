RED = \033[0;31m
GREEN = \033[0;32m
YELLOW = \033[1;33m
NC = \033[0m

PYTHON_VERSION = 3.10
ROOT_RELATIVE_DIR = .
PIP_SOURCE = https://pypi.tuna.tsinghua.edu.cn/simple

VENV_NAME = $(ROOT_RELATIVE_DIR)/.vscode/.venv
PIP = $(VENV_NAME)/bin/pip
UV = $(VENV_NAME)/bin/uv
UV_VENV = .venv_uv
REQ_IN = requirements.in
REQ_TXT = requirements.txt

PYTHON = $(UV_VENV)/bin/python

# install uv into a local .vscode/.venv
.PHONY: uv
uv:
	/usr/bin/python$(PYTHON_VERSION) -m venv $(VENV_NAME)
	@echo "${YELLOW}source $(UV_VENV)/bin/activate$(NC)"
	$(PIP) install --upgrade pip setuptools -i $(PIP_SOURCE)
	$(PIP) install --upgrade uv -i $(PIP_SOURCE)

# compile requirements.in and generate requirements.txt
.PHONY: requirements
requirements: uv
	$(UV) pip compile $(REQ_IN) -i $(PIP_SOURCE) --universal --output-file $(REQ_TXT)

# install and sync requirements.txt to current virtual env
# you have to refresh your requirements.txt by running `make requirements`
.PHONY: venv
venv: 
	UV_VENV_CLEAR=1 $(UV) venv $(UV_VENV) --python /usr/bin/python$(PYTHON_VERSION)
	VIRTUAL_ENV=$(UV_VENV) UV_LINK_MODE=copy $(UV) pip sync $(REQ_TXT) -i $(PIP_SOURCE)
	@echo "${YELLOW}source $(UV_VENV)/bin/activate$(NC)"

.PHONY: clean
clean:
	@if [ -n "$$(git status --porcelain)" ]; then \
	    echo "${RED}Warning: Uncommitted changes or untracked files will be deleted.$(NC)"; \
	    printf "${YELLOW}Are you sure?$(NC) (yes/no) "; \
	    read confirm; \
	    if [ "$$confirm" = "yes" ]; then \
	        git clean -xfd; \
	        echo "Clean completed."; \
	    else \
	        echo "Clean aborted."; \
	    fi \
	else \
	    git clean -xfd; \
	    echo "Clean completed."; \
	fi


# Add a `make format` target
.PHONY: format
format:
	@echo "TODO: implement format target"

# Generate config for the default DC/host using tools/gen_config.py
.PHONY: config
config:
	$(PYTHON) tools/gen_config.py

# Prepare mock binaries layout based on binaries/*.yaml
.PHONY: binaries
binaries:
	$(PYTHON) tools/gen_binaries.py

.PHONY: test
test:
	$(PYTHON) -m unittest discover -s tests -v