import { useState } from "react";
import { FramePicker } from "./components/FramePicker";
import { FrameView } from "./components/FrameView";

export default function App() {
  const [host, setHost] = useState<string | null>(null);

  if (!host) return <FramePicker onSelect={setHost} />;
  return <FrameView host={host} onBack={() => setHost(null)} />;
}
