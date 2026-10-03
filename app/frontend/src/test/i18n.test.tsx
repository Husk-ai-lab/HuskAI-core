import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import en from "@i18n/en.json";
import itCatalogue from "@i18n/it.json";
import { detectLang, translate } from "../i18n";

const placeholders = (s: string) => new Set([...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]));

describe("catalogues", () => {
  it("Italian has exactly the English keys and placeholders", () => {
    expect(Object.keys(itCatalogue).sort()).toEqual(Object.keys(en).sort());
    for (const [k, v] of Object.entries(en)) expect(placeholders((itCatalogue as Record<string, string>)[k])).toEqual(placeholders(v));
  });
});

describe("translate", () => {
  it("interpolates, pluralises and falls back", () => {
    expect(translate("en", "knowledge.rows", { count: 1 })).toBe("1 row");
    expect(translate("en", "knowledge.rows", { count: 4 })).toBe("4 rows");
    expect(translate("it", "knowledge.rows", { count: 4 })).toBe("4 righe");
    expect(translate("it", "chat.selected", { label: "Analisi" })).toBe("Selezionato: Analisi");
    expect(translate("it", "missing.key")).toBe("missing.key");
  });

  it("detects the browser language before login", () => {
    localStorage.clear();
    const spy = vi.spyOn(navigator, "languages", "get").mockReturnValue(["it-IT", "en"]);
    expect(detectLang()).toBe("it");
    spy.mockReturnValue(["fr-FR"]);
    expect(detectLang()).toBe("en");
    localStorage.setItem("husk.lang", "it");
    expect(detectLang()).toBe("it"); // an explicit choice wins
    spy.mockRestore();
  });
});

/**
 * Guard: user-visible text must come from the catalogues. Parses every .tsx with the TypeScript
 * compiler and fails on JSX text, or string literals in user-facing attributes, containing letters.
 */
describe("no hard-coded UI text", () => {
  const ALLOWED = new Set(["Husk AI", "Husk Studio", "Husk", "MCP", "PDF · DOCX · XLSX · CSV · PPTX · MD · DB"]);
  const USER_FACING_ATTRS = new Set(["placeholder", "title", "aria-label", "alt", "label"]);
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) { if (name !== "test") walk(p); }
      else if (p.endsWith(".tsx")) files.push(p);
    }
  };
  walk(join(__dirname, ".."));

  const offenders = (file: string): string[] => {
    const sf = ts.createSourceFile(file, readFileSync(file, "utf-8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const found: string[] = [];
    const visit = (node: ts.Node) => {
      if (ts.isJsxText(node)) {
        const text = node.text.trim();
        if (/[A-Za-zÀ-ÿ]{2,}/.test(text) && !ALLOWED.has(text)) found.push(text);
      }
      if (ts.isJsxAttribute(node) && USER_FACING_ATTRS.has(node.name.getText(sf)) && node.initializer && ts.isStringLiteral(node.initializer)) {
        const text = node.initializer.text;
        if (/[A-Za-zÀ-ÿ]{2,}/.test(text) && !ALLOWED.has(text)) found.push(`${node.name.getText(sf)}="${text}"`);
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
    return found;
  };

  it("finds the source files", () => expect(files.length).toBeGreaterThan(15));

  it.each(files)("%s", (file: string) => {
    expect(offenders(file)).toEqual([]);
  });
});
