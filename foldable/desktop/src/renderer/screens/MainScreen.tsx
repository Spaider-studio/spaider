import ChatArea from "../components/chat/ChatArea";
import SettingsDialog from "../components/settings/SettingsDialog";
import Sidebar from "../components/Sidebar";

export default function MainScreen() {
  return (
    <div className="h-full w-full flex overflow-hidden">
      <Sidebar />
      <ChatArea />
      <SettingsDialog />
    </div>
  );
}
