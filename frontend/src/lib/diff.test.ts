import { diffWords, stripTags } from "./diff";

describe("diffWords", () => {
  it("détecte un mot remplacé", () => {
    const d = diffWords("Le mimistère de la défense", "Le ministère de la défense");
    expect(d.filter((p) => p.kind === "del").map((p) => p.text)).toEqual(["mimistère"]);
    expect(d.filter((p) => p.kind === "add").map((p) => p.text)).toEqual(["ministère"]);
    expect(d.map((p) => p.text).join("")).toContain("Le ");
  });
  it("texte identique : aucune différence", () => {
    expect(diffWords("a b c", "a b c").every((p) => p.kind === "same")).toBe(true);
  });
  it("arabe", () => {
    const d = diffWords("المادة 12 من القانوم", "المادة 12 من القانون");
    expect(d.some((p) => p.kind === "del" && p.text === "القانوم")).toBe(true);
  });
});

describe("stripTags", () => {
  it("retire les balises et garde les sauts", () => {
    expect(stripTags("<p>Un <b>mot</b></p><p>Deux</p>")).toBe("Un mot\nDeux");
    expect(stripTags("<table><tr><td>A</td><td>B</td></tr></table>")).toBe("A B");
  });
});
