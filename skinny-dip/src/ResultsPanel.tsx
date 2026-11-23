import { Panel } from "@xyflow/react";
import { useEffect, type CSSProperties } from "react";

import { apiUrl } from "./api";
import { RELATION_STYLE } from "./relations";
import { isImage, isPassage, useGraph } from "./store/graphs";
import { resultFlowId } from "./utils";

/** Results of the last action run on a node; picking one adds it to the canvas. */
export function ResultsPanel() {
    const picker = useGraph(s => s.picker);
    const results = useGraph(s => (s.picker ? s.results[s.picker.key] : undefined));
    const source = useGraph(s => s.nodes.find(n => n.id === s.picker?.sourceId));
    const nodes = useGraph(s => s.nodes);
    const chosen = useGraph(s => s.chosen);
    const choose = useGraph(s => s.choose);
    const close = useGraph(s => s.closePicker);

    useEffect(() => {
        if (!picker) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [picker, close]);

    if (!picker) return null;

    const style = RELATION_STYLE[picker.relation];
    const onCanvas = new Set(nodes.map(n => n.id));
    const linked = new Set(chosen[`${picker.sourceId}:${picker.relation}`] ?? []);

    return (
        <Panel position="center-right" className="nowheel">
            <div id="results-panel" style={{ "--c": style.colour } as CSSProperties}>
                <div className="panel-head">
                    <div className="results-title">
                        <span className="relation">{style.label}</span>
                        <button className="close" title="Close (Esc)" onClick={close}>×</button>
                    </div>
                    {source && (isPassage(source) || isImage(source)) &&
                        <div className="results-source" title={isPassage(source) ? source.data.text : source.data.description}>of {source.data.title}</div>}
                    <div className="count">
                        {picker.status === "loading" && "Loading…"}
                        {picker.notice && <span className="error">{picker.notice}</span>}
                        {picker.status === "ready" && results && results.length > 0 && `${results.length} results · pick one to add it`}
                    </div>
                </div>
                <div className="result-list">
                    {results?.map(r => {
                        const id = resultFlowId(r);
                        const state = linked.has(id) ? "linked" : onCanvas.has(id) ? "on-canvas" : "";
                        return (
                            <button key={r.id} className={`result ${state}${r.type === "image" ? " result-image" : ""}`} onClick={() => choose(r)} title={r.why}>
                                <div className="result-meta">
                                    <span>{r.kind}{r.role ? ` · ${r.role}` : ""}{r.holder ? ` · ${r.holder.name}` : ""}</span>
                                    <span>
                                        {state === "linked" ? "added" : state === "on-canvas" ? "on canvas" : ""}
                                        {" "}{r.score.toFixed(2)}
                                    </span>
                                </div>
                                {r.type === "image" && r.url && <img className="result-thumb" src={apiUrl(r.url)} alt={r.title} loading="lazy" />}
                                <div className="result-title">{r.title}</div>
                                {r.type === "image" && r.credit && <div className="result-credit">© {r.credit}{r.license ? ` · ${r.license}` : ""}</div>}
                                <div className={`result-text${r.provenance === "verbatim" ? " verbatim" : ""}`}>{r.text}</div>
                                {r.why && <div className="result-why">{r.why}</div>}
                                {r.document && <div className="result-doc">{r.document}</div>}
                            </button>
                        );
                    })}
                </div>
            </div>
        </Panel>
    );
}
