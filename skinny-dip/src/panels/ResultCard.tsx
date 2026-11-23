import type { ReactNode } from "react";

import type { ActionResult } from "../api";

/** One result in the results or Ask panel: a meta line ("label · kind · role · holder" on the left, `state` on
 *  the right), title, text, why and document. Images add a `thumb` above the title and a `credit` below it. */
export function ResultCard({ result: r, className = "", label, state, disabled, onClick, thumb, credit }: {
    result: ActionResult, className?: string, label?: string, state: ReactNode,
    disabled?: boolean, onClick: () => void, thumb?: ReactNode, credit?: ReactNode,
}) {
    return (
        <button className={`result ${className}`} onClick={onClick} title={r.why} disabled={disabled}>
            <div className="result-meta">
                <span>{label && `${label} · `}{r.kind}{r.role && ` · ${r.role}`}{r.holder && ` · ${r.holder.name}`}</span>
                <span>{state}</span>
            </div>
            {thumb}
            <div className="result-title">{r.title}</div>
            {credit}
            <div className={`result-text${r.provenance === "verbatim" ? " verbatim" : ""}`}>{r.text}</div>
            {r.why && <div className="result-why">{r.why}</div>}
            {r.document && <div className="result-doc">{r.document}</div>}
        </button>
    );
}

