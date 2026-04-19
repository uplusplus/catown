import { useEffect, useState } from "react";

import type { ChatSummary, ProjectSummary } from "../types";

type AppSidebarProps = {
  chats: ChatSummary[];
  selectedChatId: number | null;
  onSelectChat: (chatId: number) => void;
  onCreateChat: () => Promise<void>;
  onRenameChat: (chatId: number, title: string) => Promise<void>;
  onDeleteChat: (chatId: number) => Promise<void>;
  projects: ProjectSummary[];
  selectedProjectId: number | null;
  onSelectProject: (projectId: number) => void;
  onQuickCreateProject: () => Promise<void>;
  onCreateProjectChat: (projectId: number) => Promise<void>;
  onReorderProjects: (draggedProjectId: number, targetProjectId: number) => Promise<void>;
  onRenameProject: (projectId: number, name: string) => Promise<void>;
  onDeleteProject: (projectId: number) => Promise<void>;
  drawerOpen?: boolean;
  onCloseDrawer?: () => void;
};

function RoomMenu({
  isOpen,
  isRenaming,
  value,
  onValueChange,
  onToggle,
  onStartRename,
  onCancelRename,
  onSubmitRename,
  onDelete,
  label,
}: {
  isOpen: boolean;
  isRenaming: boolean;
  value: string;
  onValueChange: (value: string) => void;
  onToggle: () => void;
  onStartRename: () => void;
  onCancelRename: () => void;
  onSubmitRename: () => void;
  onDelete: () => void;
  label: string;
}) {
  return (
    <div
      className={`room-entry__menu ${isOpen ? "is-open" : ""}`}
      onClick={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        className={`room-entry__menu-trigger ${isOpen ? "is-open" : ""}`}
        onClick={(event) => {
          event.stopPropagation();
          onToggle();
        }}
        aria-label={`Open actions for ${label}`}
        title="More"
      >
        ⋮
      </button>

      {isOpen ? (
        <div
          className="room-entry__menu-popover"
          onClick={(event) => event.stopPropagation()}
          onMouseDown={(event) => event.stopPropagation()}
        >
          {isRenaming ? (
            <form
              className="room-entry__rename-form"
              onSubmit={(event) => {
                event.preventDefault();
                onSubmitRename();
              }}
            >
              <input
                className="room-entry__rename-input"
                value={value}
                onChange={(event) => onValueChange(event.target.value)}
                autoFocus
                onClick={(event) => event.stopPropagation()}
              />
              <div className="room-entry__rename-actions">
                <button type="submit" className="room-entry__menu-item">
                  保存
                </button>
                <button type="button" className="room-entry__menu-item" onClick={onCancelRename}>
                  取消
                </button>
              </div>
            </form>
          ) : (
            <>
              <button type="button" className="room-entry__menu-item" onClick={onStartRename}>
                修改名称
              </button>
              <button type="button" className="room-entry__menu-item room-entry__menu-item--danger" onClick={onDelete}>
                删除
              </button>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function AppSidebar({
  chats,
  selectedChatId,
  onSelectChat,
  onCreateChat,
  onRenameChat,
  onDeleteChat,
  projects,
  selectedProjectId,
  onSelectProject,
  onQuickCreateProject,
  onCreateProjectChat,
  onReorderProjects,
  onRenameProject,
  onDeleteProject,
  drawerOpen = false,
  onCloseDrawer,
}: AppSidebarProps) {
  const [openMenuKey, setOpenMenuKey] = useState<string | null>(null);
  const [renameKey, setRenameKey] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [draggedProjectId, setDraggedProjectId] = useState<number | null>(null);
  const [dropTargetProjectId, setDropTargetProjectId] = useState<number | null>(null);

  useEffect(() => {
    function handleCloseMenu() {
      setOpenMenuKey(null);
      setRenameKey(null);
    }

    window.addEventListener("click", handleCloseMenu);
    return () => window.removeEventListener("click", handleCloseMenu);
  }, []);

  const legacyChats = chats.filter((chat) => !chat.project_id);
  const hasLegacyChats = legacyChats.length > 0;

  return (
    <aside className={`app-sidebar ${drawerOpen ? "is-mobile-open" : ""}`}>
      <div className="brand-block">
        <div className="sidebar-brand">
          <div className="brand-mark">CA</div>
          <div>
            <p className="brand-kicker">Catown</p>
            <h1>Self Bootstrap</h1>
          </div>
        </div>
        {onCloseDrawer ? (
          <button
            type="button"
            className="app-sidebar__close"
            onClick={onCloseDrawer}
            aria-label="Close sidebar"
            title="Close sidebar"
          >
            ×
          </button>
        ) : null}
      </div>

      <section className="sidebar-section">
        <div className="section-heading">
          <h2>Projects</h2>
          <div className="section-heading__actions">
            <button
              type="button"
              className="section-heading__icon-btn"
              onClick={() => void onQuickCreateProject()}
              aria-label="Create project"
              title="Create project"
            >
              +
            </button>
          </div>
        </div>

        <div className="room-list">
          {projects.length === 0 ? (
            <div className="empty-card">Preparing self-bootstrap workspace...</div>
          ) : (
            projects.map((project) => {
              const menuKey = `project-${project.id}`;
              const isMenuOpen = openMenuKey === menuKey;
              const isRenaming = renameKey === menuKey;
              const projectChats = chats.filter((chat) => chat.project_id === project.id);

              return (
                <div
                  key={project.id}
                  className={`room-entry room-entry--project ${dropTargetProjectId === project.id ? "is-drop-target" : ""}`}
                  draggable
                  onDragStart={(event) => {
                    setDraggedProjectId(project.id);
                    setDropTargetProjectId(project.id);
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", String(project.id));
                  }}
                  onDragOver={(event) => {
                    if (draggedProjectId === null || draggedProjectId === project.id) return;
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                    setDropTargetProjectId(project.id);
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    const sourceProjectId = draggedProjectId ?? Number.parseInt(event.dataTransfer.getData("text/plain"), 10);
                    if (!Number.isFinite(sourceProjectId) || sourceProjectId === project.id) {
                      setDraggedProjectId(null);
                      setDropTargetProjectId(null);
                      return;
                    }
                    void onReorderProjects(sourceProjectId, project.id);
                    setDraggedProjectId(null);
                    setDropTargetProjectId(null);
                  }}
                  onDragEnd={() => {
                    setDraggedProjectId(null);
                    setDropTargetProjectId(null);
                  }}
                >
                  <div className={`room-item room-entry__main ${selectedProjectId === project.id ? "is-active" : ""} ${isMenuOpen ? "is-menu-open" : ""}`}>
                    <button
                      type="button"
                      className="room-entry__select"
                      onClick={() => onSelectProject(project.id)}
                    >
                      <span className="room-entry__indicator room-entry__indicator--drag" aria-hidden="true">
                        ⋮⋮
                      </span>
                      <span className="room-entry__title-row">
                        <span className="room-entry__project-icon" aria-hidden="true">
                          <svg viewBox="0 0 16 16" fill="none">
                            <path
                              d="M1.75 4.25A1.5 1.5 0 0 1 3.25 2.75H6.1c.34 0 .66.14.9.38l.92.92c.23.24.56.37.9.37h3.93a1.5 1.5 0 0 1 1.5 1.5v5.88a1.5 1.5 0 0 1-1.5 1.5H3.25a1.5 1.5 0 0 1-1.5-1.5V4.25Z"
                              stroke="currentColor"
                              strokeWidth="1.2"
                              strokeLinejoin="round"
                            />
                            <path
                              d="M1.75 5.5h12.5"
                              stroke="currentColor"
                              strokeWidth="1.2"
                              strokeLinecap="round"
                            />
                          </svg>
                        </span>
                        <strong>{project.name}</strong>
                      </span>
                      <small>{project.status} / main chat</small>
                    </button>

                    <RoomMenu
                      isOpen={isMenuOpen}
                      isRenaming={isRenaming}
                      value={renameValue}
                      onValueChange={setRenameValue}
                      onToggle={() => setOpenMenuKey((current) => (current === menuKey ? null : menuKey))}
                      onStartRename={() => {
                        setRenameKey(menuKey);
                        setRenameValue(project.name);
                      }}
                      onCancelRename={() => setRenameKey(null)}
                      onSubmitRename={() => {
                        const nextName = renameValue.trim();
                        if (!nextName) return;
                        void onRenameProject(project.id, nextName);
                        setOpenMenuKey(null);
                        setRenameKey(null);
                      }}
                      onDelete={() => {
                        setOpenMenuKey(null);
                        setRenameKey(null);
                        void onDeleteProject(project.id);
                      }}
                      label={project.name}
                    />

                    <button
                      type="button"
                      className="room-entry__quick-action"
                      onClick={() => void onCreateProjectChat(project.id)}
                      aria-label={`Create chat in ${project.name}`}
                      title="New sub chat"
                    >
                      +
                    </button>
                  </div>

                  {projectChats.length > 0 ? (
                    <div className="project-subchat-list">
                      {projectChats.map((chat) => {
                        const childMenuKey = `chat-${chat.id}`;
                        const isChildMenuOpen = openMenuKey === childMenuKey;
                        const isChildRenaming = renameKey === childMenuKey;
                        return (
                          <div key={chat.id} className="room-entry room-entry--child">
                            <div className={`room-item room-entry__main room-entry__main--secondary ${selectedChatId === chat.id ? "is-active" : ""} ${isChildMenuOpen ? "is-menu-open" : ""}`}>
                              <button type="button" className="room-entry__select" onClick={() => onSelectChat(chat.id)}>
                                <span className="room-entry__indicator room-entry__indicator--subchat" aria-hidden="true" />
                                <span className="room-entry__title-row">
                                  <strong>{chat.title}</strong>
                                </span>
                                <small>sub chat</small>
                              </button>

                              <RoomMenu
                                isOpen={isChildMenuOpen}
                                isRenaming={isChildRenaming}
                                value={renameValue}
                                onValueChange={setRenameValue}
                                onToggle={() => setOpenMenuKey((current) => (current === childMenuKey ? null : childMenuKey))}
                                onStartRename={() => {
                                  setRenameKey(childMenuKey);
                                  setRenameValue(chat.title);
                                }}
                                onCancelRename={() => setRenameKey(null)}
                                onSubmitRename={() => {
                                  const nextTitle = renameValue.trim();
                                  if (!nextTitle) return;
                                  void onRenameChat(chat.id, nextTitle);
                                  setOpenMenuKey(null);
                                  setRenameKey(null);
                                }}
                                onDelete={() => {
                                  setOpenMenuKey(null);
                                  setRenameKey(null);
                                  void onDeleteChat(chat.id);
                                }}
                                label={chat.title}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </section>

      {hasLegacyChats ? (
        <section className="sidebar-section sidebar-section--secondary">
          <div className="section-heading">
            <h2>Legacy Chats</h2>
            <div className="section-heading__actions">
              <button
                type="button"
                className="section-heading__icon-btn"
                onClick={() => void onCreateChat()}
                aria-label="Open self-bootstrap project"
                title="Open self-bootstrap project"
              >
                ↗
              </button>
            </div>
          </div>

          <div className="room-list">
            {legacyChats.map((chat) => {
              const menuKey = `chat-${chat.id}`;
              const isMenuOpen = openMenuKey === menuKey;
              const isRenaming = renameKey === menuKey;

              return (
                <div key={chat.id} className="room-entry">
                  <div
                    className={`room-item room-entry__main room-entry__main--secondary ${
                      selectedChatId === chat.id ? "is-active" : ""
                    } ${isMenuOpen ? "is-menu-open" : ""}`}
                  >
                    <button type="button" className="room-entry__select" onClick={() => onSelectChat(chat.id)}>
                      <strong>{chat.title}</strong>
                      <small>{chat.updated_at ? "standalone chat" : "legacy chat"}</small>
                    </button>

                    <RoomMenu
                      isOpen={isMenuOpen}
                      isRenaming={isRenaming}
                      value={renameValue}
                      onValueChange={setRenameValue}
                      onToggle={() => setOpenMenuKey((current) => (current === menuKey ? null : menuKey))}
                      onStartRename={() => {
                        setRenameKey(menuKey);
                        setRenameValue(chat.title);
                      }}
                      onCancelRename={() => setRenameKey(null)}
                      onSubmitRename={() => {
                        const nextTitle = renameValue.trim();
                        if (!nextTitle) return;
                        void onRenameChat(chat.id, nextTitle);
                        setOpenMenuKey(null);
                        setRenameKey(null);
                      }}
                      onDelete={() => {
                        setOpenMenuKey(null);
                        setRenameKey(null);
                        void onDeleteChat(chat.id);
                      }}
                      label={chat.title}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ) : null}
    </aside>
  );
}
