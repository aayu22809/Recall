import { useState, useRef, useCallback } from "react";
import { Grid, ActionPanel, Action, Icon, Color, environment, getPreferenceValues, showToast, Toast, openExtensionPreferences } from "@raycast/api";
import { execSync } from "child_process";
import { existsSync, mkdirSync } from "fs";
import { join } from "path";

interface Preferences {
  pythonPackagePath: string;
  pythonPath?: string;
}

interface SearchResult {
  id: string;
  similarity: number;
  file_path: string;
  file_name: string;
  media_category: string;
  timestamp: string;
  description: string;
  source: string;
  preview: string;
}

const THUMB_DIR = join(environment.supportPath, "thumbnails");

try {
  mkdirSync(THUMB_DIR, { recursive: true });
} catch {
  // ignore
}

function getCategoryIcon(category: string): { source: Icon; tintColor: Color } {
  switch (category) {
    case "image":
      return { source: Icon.Image, tintColor: Color.Blue };
    case "audio":
      return { source: Icon.Microphone, tintColor: Color.Purple };
    case "video":
      return { source: Icon.Video, tintColor: Color.Red };
    case "document":
      return { source: Icon.Document, tintColor: Color.Orange };
    case "text":
      return { source: Icon.Text, tintColor: Color.Green };
    default:
      return { source: Icon.QuestionMarkCircle, tintColor: Color.SecondaryText };
  }
}

function getVideoThumbnail(filePath: string, fileId: string): string | null {
  const thumbPath = join(THUMB_DIR, `${fileId}.jpg`);
  if (existsSync(thumbPath)) return thumbPath;

  try {
    execSync(
      `ffmpeg -y -i "${filePath}" -ss 00:00:01 -frames:v 1 -q:v 2 "${thumbPath}" 2>/dev/null`,
      { timeout: 5000, encoding: "utf-8" },
    );
    return existsSync(thumbPath) ? thumbPath : null;
  } catch {
    return null;
  }
}

function validateSetup(): string | null {
  const prefs = getPreferenceValues<Preferences>();
  const pkgPath = prefs.pythonPackagePath;

  if (!pkgPath || !pkgPath.trim()) {
    return "Python Package Path is not set. Open extension preferences and set the path to the vector-embedded-finder repo.";
  }

  if (!existsSync(pkgPath)) {
    return `Python Package Path does not exist: ${pkgPath}`;
  }

  if (!existsSync(join(pkgPath, "vector_embedded_finder"))) {
    return `"vector_embedded_finder/" not found in ${pkgPath}. The path should point to the repo root containing the vector_embedded_finder/ directory.`;
  }

  const python = prefs.pythonPath || "python3";
  try {
    execSync(`${python} --version`, { timeout: 5000, encoding: "utf-8" });
  } catch {
    return `Python binary not found: ${python}. Set the correct path in extension preferences.`;
  }

  try {
    execSync(
      `${python} -c "import sys; sys.path.insert(0, '${pkgPath}'); from vector_embedded_finder.search import search"`,
      { timeout: 10000, encoding: "utf-8", env: { ...process.env, PATH: process.env.PATH || "/usr/bin:/usr/local/bin" } },
    );
  } catch (e: unknown) {
    const stderr = e instanceof Error && "stderr" in e ? String((e as { stderr: unknown }).stderr) : "";
    if (stderr.includes("No module named")) {
      const match = stderr.match(/No module named '([^']+)'/);
      return `Missing Python dependency: ${match?.[1] || "unknown"}. Run "pip install -e ." in the repo root.`;
    }
    return `Python import failed. Run "pip install -e ." in ${pkgPath}. Error: ${stderr.slice(0, 200)}`;
  }

  return null;
}

function runSearch(query: string, count: number = 20): SearchResult[] {
  if (!query.trim()) return [];

  const prefs = getPreferenceValues<Preferences>();
  const python = prefs.pythonPath || "python3";
  const pkgPath = prefs.pythonPackagePath;
  const safeQuery = query.replace(/\\/g, "\\\\").replace(/'/g, "\\'");

  try {
    const output = execSync(
      `${python} -c "
import json, sys
sys.path.insert(0, '${pkgPath}')
from vector_embedded_finder.search import search
results = search('${safeQuery}', n_results=${count})
print(json.dumps(results))
"`,
      { timeout: 15000, encoding: "utf-8" },
    );

    return JSON.parse(output.trim());
  } catch {
    return [];
  }
}

export default function SearchMemory() {
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [setupError, setSetupError] = useState<string | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const cacheRef = useRef<Map<string, SearchResult[]>>(new Map());
  const validatedRef = useRef(false);

  const handleSearchChange = useCallback((text: string) => {
    setSearchText(text);
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!text.trim()) {
      setResults([]);
      setIsLoading(false);
      return;
    }

    if (!validatedRef.current) {
      validatedRef.current = true;
      const error = validateSetup();
      if (error) {
        setSetupError(error);
        setIsLoading(false);
        showToast({ style: Toast.Style.Failure, title: "Setup Error", message: error });
        return;
      }
    }

    if (setupError) return;

    const cached = cacheRef.current.get(text.trim().toLowerCase());
    if (cached) {
      setResults(cached);
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    timerRef.current = setTimeout(() => {
      const r = runSearch(text, 20);
      cacheRef.current.set(text.trim().toLowerCase(), r);
      setResults(r);
      setIsLoading(false);
    }, 400);
  }, [setupError]);

  if (setupError) {
    return (
      <Grid columns={4} searchBarPlaceholder="Search memory...">
        <Grid.EmptyView
          icon={Icon.ExclamationMark}
          title="Setup Required"
          description={setupError}
          actions={
            <ActionPanel>
              <Action title="Open Extension Preferences" icon={Icon.Gear} onAction={openExtensionPreferences} />
            </ActionPanel>
          }
        />
      </Grid>
    );
  }

  return (
    <Grid
      columns={4}
      fit={Grid.Fit.Fill}
      inset={Grid.Inset.Zero}
      isLoading={isLoading}
      searchBarPlaceholder="Search memory..."
      onSearchTextChange={handleSearchChange}
    >
      {results.length === 0 && searchText.trim() !== "" && !isLoading ? (
        <Grid.EmptyView icon={Icon.MagnifyingGlass} title="No results found" description="Try a different query" />
      ) : null}

      {results.map((result, index) => {
        const icon = getCategoryIcon(result.media_category);
        const score = `${(result.similarity * 100).toFixed(1)}%`;
        const fileExists = result.file_path ? existsSync(result.file_path) : false;

        let contentSource: string | null = null;
        if (fileExists) {
          if (result.media_category === "image") {
            contentSource = result.file_path;
          } else if (result.media_category === "video") {
            contentSource = getVideoThumbnail(result.file_path, result.id);
          }
        }

        const content: Grid.Item.Content = contentSource
          ? { source: contentSource }
          : { source: icon.source, tintColor: icon.tintColor };

        return (
          <Grid.Item
            key={result.id || `result-${index}`}
            content={content}
            title={result.file_name || "Text snippet"}
            subtitle={`#${index + 1} - ${score}`}
            actions={
              <ActionPanel>
                {result.file_path && fileExists && (
                  <>
                    <Action.Open title="Open File" target={result.file_path} />
                    <Action.ShowInFinder path={result.file_path} />
                  </>
                )}
                <Action.CopyToClipboard
                  title="Copy Path"
                  content={result.file_path || result.preview || ""}
                  shortcut={{ modifiers: ["cmd"], key: "c" }}
                />
                {result.preview && (
                  <Action.CopyToClipboard
                    title="Copy Preview Text"
                    content={result.preview}
                    shortcut={{ modifiers: ["cmd", "shift"], key: "c" }}
                  />
                )}
              </ActionPanel>
            }
          />
        );
      })}
    </Grid>
  );
}
