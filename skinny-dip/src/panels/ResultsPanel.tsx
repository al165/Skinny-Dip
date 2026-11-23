import { Panel } from "@xyflow/react";

import { apiUrl } from "../api";
import { resultFlowId } from "../lib/canvas";
import { useEscape } from "../lib/hooks";
import { accent, RELATION_STYLE } from "../relations";
import { useGraph } from "../store/graphs";
import { isImage, isPassage } from "../types";
import { ResultCard } from "./ResultCard";

/** Results of the last move run on a node; picking one adds it to the canvas. */
export function ResultsPanel() {
    const picker = useGraph(s => s.picker);
    const results = useGraph(s => (s.picker ? s.results[s.picker.key] : undefined));
    const source = useGraph(s => s.nodes.find(n => n.id === s.picker?.sourceId));
    const nodes = useGraph(s => s.nodes);
    const chosen = useGraph(s => s.chosen);
    const choose = useGraph(s => s.choose);
    const close = useGraph(s => s.closePicker);
    useEscape(!!picker, close);

    if (!picker) return null;

    const style = RELATION_STYLE[picker.relation];
    const onCanvas = new Set(nodes.map(n => n.id));
    const linked = new Set(chosen[`${picker.sourceId}:${picker.relation}`] ?? []);

    return (
        <Panel position="center-right" className="nowheel">
            <div id="results-panel" style={accent(style.colour)}>
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
                        const image = r.type === "image";
                        return (
                            <ResultCard key={r.id} result={r} onClick={() => choose(r)}
                                className={`${state}${image ? " result-image" : ""}`}
                                state={<>{state === "linked" ? "added" : state === "on-canvas" ? "on canvas" : ""} {r.score.toFixed(2)}</>}
                                thumb={image && r.url && <img className="result-thumb" src={apiUrl(r.url)} alt={r.title} loading="lazy" />}
                                credit={image && r.credit && <div className="result-credit">© {r.credit}{r.license ? ` · ${r.license}` : ""}</div>}
                            />
                        );
                    })}
                </div>
            </div>
        </Panel>
    );
}
