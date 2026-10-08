import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DeleteDataModal } from "../components/DeleteDataModal";
import { I18nProvider, type Lang } from "../i18n";

const show = (lang: Lang, onConfirm = vi.fn()) => {
  render(<I18nProvider initial={lang}><DeleteDataModal busy={false} onConfirm={onConfirm} onClose={() => {}} /></I18nProvider>);
  return onConfirm;
};

describe("DeleteDataModal", () => {
  it("stays disabled until the word is typed (English)", () => {
    const onConfirm = show("en");
    const go = screen.getByRole("button", { name: "Delete everything" });
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByLabelText("DELETE"), { target: { value: "delete me" } });
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByLabelText("DELETE"), { target: { value: "delete" } });  // case does not matter
    expect(go).toBeEnabled();
    fireEvent.click(go);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("asks for the Italian word in Italian", () => {
    show("it");
    const go = screen.getByRole("button", { name: "Elimina tutto" });
    fireEvent.change(screen.getByLabelText("ELIMINA"), { target: { value: "DELETE" } });
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByLabelText("ELIMINA"), { target: { value: "elimina" } });
    expect(go).toBeEnabled();
  });
});
