import { Circle, Wifi, WifiOff } from "lucide-react";
import type { ServerStatus } from "../types";

interface Props {
  status: ServerStatus;
  wsState: "connecting" | "open" | "closed";
}

export function StatusBar({ status, wsState }: Props) {
  return (
    <header className="status-bar">
      <div className="status-left">
        <span className="brand-dot" />
        <span>Codex Remote</span>
      </div>
      <div className="status-items">
        <span className={status.ipc.online ? "status-item ok" : "status-item muted"}>
          {status.ipc.online ? <Wifi size={15} /> : <WifiOff size={15} />}
          IPC {status.ipc.online ? "online" : "offline"}
        </span>
        <span className={status.sdk.available ? "status-item ok" : "status-item muted"}>
          <Circle size={11} fill="currentColor" />
          SDK {status.sdk.available ? "ready" : "unavailable"}
        </span>
        <span className={wsState === "open" ? "status-item ok" : "status-item muted"}>
          <Circle size={11} fill="currentColor" />
          WS {wsState}
        </span>
      </div>
    </header>
  );
}

