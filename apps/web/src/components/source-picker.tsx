"use client";

/** Checkbox tree over the sources hierarchy. Shared by bot creation and settings. */

export type SourceNode = {
  id: number;
  type: "directory" | "file" | "web";
  name: string;
  status: string;
  children: SourceNode[];
};

export function SourcePicker({
  nodes,
  selected,
  onToggle,
  depth = 0,
}: {
  nodes: SourceNode[];
  selected: number[];
  onToggle: (id: number) => void;
  depth?: number;
}) {
  return (
    <>
      {nodes.map((n) => (
        <div key={n.id}>
          <label
            className="flex items-center gap-2 cursor-pointer hover:bg-slate-50 py-0.5 px-1 rounded"
            style={{ paddingLeft: depth * 12 + 4 }}
          >
            <input
              type="checkbox"
              checked={selected.includes(n.id)}
              onChange={() => onToggle(n.id)}
            />
            <span className={n.type === "directory" ? "font-medium" : ""}>{n.name}</span>
            <span className="text-[10px] uppercase text-slate-400">{n.type}</span>
          </label>
          {n.children.length > 0 && (
            <SourcePicker
              nodes={n.children}
              selected={selected}
              onToggle={onToggle}
              depth={depth + 1}
            />
          )}
        </div>
      ))}
    </>
  );
}
