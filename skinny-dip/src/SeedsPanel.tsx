import { Panel, useReactFlow } from "@xyflow/react";
import { useMemo, useState, type DragEvent } from "react";

import { type SeedKind } from "./api";
import { useGraph, type Item } from "./store/graphs";

export const DRAG_TYPE = "application/x-dip-node";

const KINDS: [SeedKind, string][] = [
    ["theme", "Themes"],
    ["issue", "Issues"],
    ["argument", "Arguments"],
    ["holder", "Holders"],
    ["image", "Images"],
];

export function SeedPanel() {
    const seeds = useGraph(s => s.seeds);
    const status = useGraph(s => s.seedsStatus);
    const error = useGraph(s => s.seedsError);
    const kind = useGraph(s => s.seedKind);
    const loadSeeds = useGraph(s => s.loadSeeds);
    const [query, setQuery] = useState("");
    const open = useGraph(s => s.sideOpen);
    const setOpen = useGraph(s => s.setSideOpen);
    const askCorpus = useGraph(s => s.askCorpus);
    const asking = useGraph(s => s.ask?.sourceId === null && s.ask.status === "loading");
    const [question, setQuestion] = useState("");
    const { screenToFlowPosition } = useReactFlow();
    const send = () => {
        if (!question.trim() || asking) return;
        // The question node goes in the middle of the screen.
        askCorpus(question, screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 3 }));
        setQuestion("");
    };

    const shown = useMemo(() => {
        const q = query.trim().toLowerCase();
        return q ? seeds.filter(s => s.text.toLowerCase().includes(q) || s.title.toLowerCase().includes(q)
            || (s.holder?.name ?? "").toLowerCase().includes(q)) : seeds;
    }, [seeds, query]);

    const onDragStart = (e: DragEvent, item: Item) => {
        e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(item));
        e.dataTransfer.effectAllowed = "move";
    };

    return (
        <Panel position="top-left" className="nowheel">
            <div className="main-title" onClick={() => setOpen(!open)} title={open ? "Collapse the panel" : "Expand the panel"}>
                {/* <span className="toggle">{open ? "▾" : "▸"}</span> */}
                Skinny Dip
                {/* <a className="wiki-link" href={apiUrl("/ui/editor.html")} target="_blank" rel="noreferrer" */}
                {/*     title="Edit issues, themes and holders" onClick={e => e.stopPropagation()}>wiki ↗</a> */}
            </div>
            {open && <div id="side-panel" >
                <div className="panel-head corpus-ask">
                    <textarea
                        className="ask-input"
                        rows={question.trim() ? 3 : 1}
                        placeholder="Ask a question about the corpus…"
                        value={question}
                        onChange={e => setQuestion(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                    />
                    <div className="ask-row">
                        <span className="count">{asking ? "Asking, please wait…" : ""}</span>
                        {question.trim() &&
                            <button className="ask-send" disabled={asking || !question.trim()} onClick={send}>
                                {asking ? "Asking…" : "Ask"}
                            </button>
                        }
                    </div>
                </div>
                <div className="panel-head">
                    <div className="tabs">
                        {KINDS.map(([k, label]) => (
                            <button key={k} className={k === kind ? "active" : ""} onClick={() => loadSeeds(k)}>
                                {label}
                            </button>
                        ))}
                    </div>
                    <input
                        type="search"
                        placeholder="Filter…"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                    />
                    <div className="count">
                        {status === "loading" && "Loading…"}
                        {status === "error" && <span className="error">Could not load: {error}</span>}
                        {status === "ready" && `${shown.length} of ${seeds.length} · drag one onto the canvas`}
                    </div>
                </div>
                <div className="seed-list">
                    {shown.map(item => item.role === "image" ? (
                        <div key={item.id} className="seed seed-image" draggable onDragStart={e => onDragStart(e, item)} title={item.text}>
                            <img src={item.url ?? undefined} alt={item.title} loading="lazy" draggable={false} />
                            <div className="seed-title">{item.title}</div>
                            {item.credit && <div className="seed-credit">{item.credit}</div>}
                        </div>
                    ) : (
                        <div key={item.id} className="seed" draggable onDragStart={e => onDragStart(e, item)} title={item.text}>
                            {/* {item.holder && <div className="seed-holder">{item.holder.name}</div>} */}
                            {/* <div className="seed-title">{item.title}</div> */}
                            <div className="seed-text">{item.title}</div>
                        </div>
                    ))}
                </div>
            </div>}
        </Panel>
    );
}
