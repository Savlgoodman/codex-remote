import { Circle, Server } from "lucide-react";
import type { ServerStatus } from "../types";

type Props = {
  status: ServerStatus | null;
  sseConnected: boolean;
};

export function StatusBar({ status, sseConnected }: Props) {
  return (
    <div className="status-strip">
      <span className={status?.ipc.online ? "ok" : "muted"}>
        <Circle size={9} fill="currentColor" />
        IPC {status?.ipc.online ? "在线" : "离线"}
      </span>
      <span className={sseConnected ? "ok" : "muted"}>
        <Server size={13} />
        SSE {sseConnected ? "已连接" : "重连中"}
      </span>
    </div>
  );
}

