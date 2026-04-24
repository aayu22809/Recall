class Recall < Formula
  include Language::Python::Virtualenv

  desc "Local semantic search across your files, email, calendar, and notes"
  homepage "https://github.com/aayu22809/Recall"
  url "https://github.com/aayu22809/Recall/archive/refs/tags/v0.3.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
  license "MIT"

  depends_on "python@3.11"

  # Core runtime dependencies
  resource "chromadb" do
    url "https://files.pythonhosted.org/packages/source/c/chromadb/chromadb-0.6.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "fastapi" do
    url "https://files.pythonhosted.org/packages/source/f/fastapi/fastapi-0.115.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "uvicorn" do
    url "https://files.pythonhosted.org/packages/source/u/uvicorn/uvicorn-0.30.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/source/h/httpx/httpx-0.27.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "python-dotenv" do
    url "https://files.pythonhosted.org/packages/source/p/python-dotenv/python_dotenv-1.0.1.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "watchdog" do
    url "https://files.pythonhosted.org/packages/source/w/watchdog/watchdog-4.0.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "psutil" do
    url "https://files.pythonhosted.org/packages/source/p/psutil/psutil-5.9.8.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/source/r/rich/rich-13.7.1.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "pypdf" do
    url "https://files.pythonhosted.org/packages/source/p/pypdf/pypdf-4.1.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  def install
    virtualenv_install_with_resources
  end

  def post_install
    ohai "Recall installed!"
    ohai "Next steps:"
    puts "  1. Run: recall doctor          # check your setup"
    puts "  2. Run: vef-setup              # interactive setup wizard"
    puts "  3. Run: recall start           # start the daemon"
    puts "  4. Run: recall index ~/Documents  # index your files"
    puts "  5. Open Raycast and search for 'Recall'"
    puts ""
    puts "  For embedding (pick one):"
    puts "    Gemini (free): add GEMINI_API_KEY=... to ~/.vef/.env"
    puts "    Ollama (local): brew install ollama && ollama pull nomic-embed-text"
  end

  test do
    system bin/"recall", "--help"
  end
end
