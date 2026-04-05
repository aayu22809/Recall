import { showHUD, open, getPreferenceValues, openExtensionPreferences } from "@raycast/api";
import { execSync } from "child_process";
import { existsSync } from "fs";
import { join } from "path";

interface Preferences {
  pythonPackagePath: string;
  pythonPath?: string;
}

interface SearchResult {
  file_path: string;
  file_name: string;
  similarity: number;
  media_category: string;
}

export default async function OpenMemory(props: { arguments: { query: string } }) {
  const { query } = props.arguments;
  const prefs = getPreferenceValues<Preferences>();
  const python = prefs.pythonPath || "python3";
  const pkgPath = prefs.pythonPackagePath;

  if (!pkgPath || !pkgPath.trim()) {
    await showHUD("⚠ Set Python Package Path in extension preferences");
    await openExtensionPreferences();
    return;
  }

  if (!existsSync(join(pkgPath, "vector_embedded_finder"))) {
    await showHUD(`⚠ vector_embedded_finder/ not found in ${pkgPath}`);
    await openExtensionPreferences();
    return;
  }

  try {
    const output = execSync(
      `${python} -c "
import json, sys
sys.path.insert(0, '${pkgPath}')
from vector_embedded_finder.search import search
results = search('''${query.replace(/'/g, "\\'")}''', n_results=1)
print(json.dumps(results))
"`,
      { timeout: 10000, encoding: "utf-8" },
    );

    const results: SearchResult[] = JSON.parse(output.trim());

    if (results.length > 0 && results[0].file_path && existsSync(results[0].file_path)) {
      const r = results[0];
      const score = (r.similarity * 100).toFixed(1);
      await open(r.file_path);
      await showHUD(`Opened: ${r.file_name} (${score}% match)`);
    } else {
      await showHUD(`No matching file found for: ${query}`);
    }
  } catch (e: unknown) {
    const stderr = e instanceof Error && "stderr" in e ? String((e as { stderr: unknown }).stderr) : "";
    if (stderr.includes("No module named")) {
      const match = stderr.match(/No module named '([^']+)'/);
      await showHUD(`⚠ Missing Python dependency: ${match?.[1] || "unknown"} — run "pip install -e ." in repo root`);
    } else {
      await showHUD("⚠ Search failed — run \"pip install -e .\" in the vector-embedded-finder repo");
    }
  }
}
