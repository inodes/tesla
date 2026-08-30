# Contributing to TeslaCam Multi-Drive Suite

Thank you for your interest in contributing to the **TeslaCam Multi-Drive Suite**! We welcome bug reports, feature requests, documentation improvements, and code contributions.

---

## 🛠️ Getting Started

1. **Fork the repository** on GitHub.
2. **Clone your fork** locally:
   ```bash
   git clone git@github.com:<your-username>/tesla.git
   cd tesla
   ```
3. **Verify your local environment**:
   ```bash
   ./tesla_sync.sh --check-deps
   ```

---

## 💡 How to Contribute

### 1. Reporting Bugs
- Check the [Issues tab](https://github.com/inodes/tesla/issues) to ensure the issue hasn't already been reported.
- Use the **Bug Report** template to provide detailed steps to reproduce, macOS version, connected drive models, and relevant terminal logs.

### 2. Suggesting Enhancements
- Open a feature request issue describing the use case and proposed CLI flag or workflow behavior.

### 3. Submitting Pull Requests (PRs)
- Create a feature branch for your changes:
  ```bash
  git checkout -b feature/my-enhancement
  ```
- Keep PRs focused on a single topic or fix.
- Ensure scripts maintain compatibility with standard macOS (`zsh` and Python 3) without requiring non-standard dependencies.
- Update documentation (`README.md`) if CLI flags or workflows are modified.
- Submit your PR against the `main` branch with a clear description of your changes.

---

## 📜 Code Style & Standards

- **Shell scripts:** Follow `zsh`/`bash` best practices, use descriptive variable names, handle missing paths gracefully, and preserve terminal styling.
- **Python:** Use clean, standard library Python 3 (3.8+) wherever possible to avoid forcing users to install external `pip` dependencies.
- **Safety First:** Pruning and purge operations must maintain the **Zero Data Loss Guarantee** by strictly verifying destination archive checksums/sizes before deletion.

---

## 🤝 Code of Conduct

Please note that this project is released with a [Contributor Code of Conduct](CODE_OF_CONDUCT.md). By participating in this project you agree to abide by its terms.
