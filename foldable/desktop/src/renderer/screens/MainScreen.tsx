import ChatArea from "../components/chat/ChatArea";
import ConsentDialog from "../components/chat/ConsentDialog";
import SettingsDialog from "../components/settings/SettingsDialog";
import Sidebar from "../components/Sidebar";
import TasksArea from "../components/tasks/TasksArea";
import { useAppStore } from "../store/useAppStore";

export default function MainScreen() {
  const mainView = useAppStore((s) => s.mainView);
  return (
    <div className="h-full w-full flex overflow-hidden">
      <Sidebar />
      {mainView === "tasks" ? <TasksArea /> : <ChatArea />}
      <SettingsDialog />
      <ConsentDialog />
    </div>
  );
}
