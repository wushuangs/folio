import { WorkspaceShell } from "@/features/workspace/workspace-shell";

export default function Layout({ children }: { children: React.ReactNode }) {
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
