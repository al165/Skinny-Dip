import { Panel } from "@xyflow/react";
import { useMemo, useState, type DragEvent } from "react";

import type { Item, SeedKind } from "../api";
import { sendOnEnter, useQuestionSpot } from "../lib/hooks";
import { useGraph } from "../store/graphs";

/** The drag payload type of a seed (the item as JSON); App's drop handler reads it. */
export const SEED_DRAG_TYPE = "application/x-dip-node";

const KINDS: [SeedKind, string][] = [
    ["theme", "Themes"],
    ["issue", "Issues"],
    ["argument", "Arguments"],
    ["holder", "Holders"],
    ["image", "Images"],
];

/** The collapsible left panel: a box to ask about the whole corpus, and the seeds to drag onto the canvas. */
export function SeedsPanel() {
    const open = useGraph(s => s.sideOpen);
    const setOpen = useGraph(s => s.setSideOpen);

    return (
        <Panel position="top-left" className="nowheel">
            <div className="main-title" onClick={() => setOpen(!open)} title={open ? "Collapse the panel" : "Expand the panel"}>
                Skinny Dip
            </div>
            {open &&
                <div id="side-panel">
                    <CorpusQuestion />
                    <SeedList />
                </div>}
        </Panel>
    );
}

function CorpusQuestion() {
    const askCorpus = useGraph(s => s.askCorpus);
    const questionSpot = useQuestionSpot();
    const [question, setQuestion] = useState("");

    const send = () => {
        if (!question.trim()) return;
        askCorpus(question, questionSpot());
        setQuestion("");
    };

    return (
        <div className="panel-head corpus-ask">
            <textarea
                className="ask-input"
                rows={question.trim() ? 3 : 1}
                placeholder="Ask a question about the corpus…"
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={sendOnEnter(send)}
            />
            {question.trim() &&
                <div className="ask-row">
                    <span className="count" />
                    <button className="ask-send" onClick={send}>Ask</button>
                </div>}
        </div>
    );
}

function SeedList() {
    const seeds = useGraph(s => s.seeds);
    const status = useGraph(s => s.seedsStatus);
    const error = useGraph(s => s.seedsError);
    const kind = useGraph(s => s.seedKind);
    const loadSeeds = useGraph(s => s.loadSeeds);
    const [query, setQuery] = useState("");

    const shown = useMemo(() => {
        const q = query.trim().toLowerCase();
        return q ? seeds.filter(s => s.text.toLowerCase().includes(q) || s.title.toLowerCase().includes(q)
            || (s.holder?.name ?? "").toLowerCase().includes(q)) : seeds;
    }, [seeds, query]);

    const onDragStart = (e: DragEvent, item: Item) => {
        e.dataTransfer.setData(SEED_DRAG_TYPE, JSON.stringify(item));
        e.dataTransfer.effectAllowed = "move";
    };

    return (
        <>
            <div className="panel-head">
                <div className="tabs">
                    {KINDS.map(([k, label]) => (
                        <button key={k} className={k === kind ? "active" : ""} onClick={() => loadSeeds(k, true)}>{label}</button>
                    ))}
                </div>
                <input type="search" placeholder="Filter…" value={query} onChange={e => setQuery(e.target.value)} />
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
                        <div className="seed-text">{item.title}</div>
                    </div>
                ))}
            </div>
        </>
    );
}
