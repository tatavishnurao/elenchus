.PHONY: install-hooks check-attribution

install-hooks:
	git config core.hooksPath hooks

# Run before every commit: shows the staged diff and scans the tree.
check-attribution:
	git diff --cached
	bash scripts/check_attribution.sh tree
