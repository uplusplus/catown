import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { format } from 'date-fns';

// ==================== Types ====================
interface Message {
  id: number;
  content: string;
  agent_name: string | null;
  message_type: string;
  created_at?: string;
}

interface Project {
  id: number;
  name: string;
  description: string;
  status: string;
  chatroom_id: number | null;
  agents: Array<{ id: number; name: string; role: string; is_active: boolean; }>;
}

interface Agent {
  id: number;
  name: string;
  role: string;
  is_active: boolean;
  status?: 'idle' | 'thinking' | 'executing';
}

interface LogEntry {
  type: 'system' | 'info' | 'success' | 'warning' | 'error' | 'api';
  message: string;
  timestamp: Date;
}

// ==================== Main Component ====================
export default function MainApp() {
  // State
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [sidePanelHidden, setSidePanelHidden] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // ==================== Effects ====================
  useEffect(() => {
    fetchProjects();
    fetchAgents();
    addLog('system', 'System initialized at ' + format(new Date(), 'HH:mm:ss') + ' UTC');
    return () => {
      if (wsRef.current) {
        try {
          if (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING) {
            wsRef.current.close();
          }
        } catch (e) {}
        wsRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    if (currentProject?.chatroom_id) {
      fetchMessages(currentProject.chatroom_id);
      connectWebSocket(currentProject.chatroom_id);
    }
    return () => {
      if (wsRef.current) {
        try {
          if (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING) {
            wsRef.current.close();
          }
        } catch (e) {}
        wsRef.current = null;
      }
    };
  }, [currentProject]);

  // ==================== API Functions ====================
  const fetchProjects = async () => {
    try {
      const response = await axios.get('/api/projects');
      setProjects(response.data);
      if (response.data.length > 0 && !currentProject) {
        setCurrentProject(response.data[0]);
        addLog('info', `[Room] ${response.data[0].name} ready`);
      }
    } catch (error) {
      addLog('error', 'Failed to fetch projects');
    }
  };

  const fetchAgents = async () => {
    try {
      const response = await axios.get('/api/agents');
      const agentsWithStatus = response.data.map((a: Agent) => ({ ...a, status: 'idle' as const }));
      setAgents(agentsWithStatus);
      response.data.forEach((a: Agent) => {
        addLog('success', `[Agent:${a.name}] Loaded role '${a.role}'`);
      });
    } catch (error) {
      addLog('error', 'Failed to fetch agents');
    }
  };

  const fetchMessages = async (chatroomId: number) => {
    try {
      const response = await axios.get(`/api/chatrooms/${chatroomId}/messages`);
      setMessages(response.data);
    } catch (error) {
      addLog('error', 'Failed to fetch messages');
    }
  };

  const createProject = async (name: string, description: string = '') => {
    try {
      const response = await axios.post('/api/projects', { name, description, agent_names: ['assistant'] });
      setProjects([...projects, response.data]);
      setCurrentProject(response.data);
      addLog('success', `[Room] ${name} created`);
      return response.data;
    } catch (error) {
      addLog('error', 'Failed to create project');
      return null;
    }
  };

  const sendMessage = async () => {
    if (!input.trim() || !currentProject?.chatroom_id || loading) return;
    const content = input.trim();
    setInput('');
    setLoading(true);

    if (content.startsWith('创建项目：') || content.startsWith('create project:')) {
      const name = content.replace(/^(创建项目：|create project:)/i, '').trim();
      await createProject(name);
      setLoading(false);
      return;
    }

    try {
      const userMessage: Message = {
        id: Date.now(),
        content,
        agent_name: null,
        message_type: 'user',
        created_at: new Date().toISOString()
      };
      setMessages(prev => [...prev, userMessage]);
      await axios.post(`/api/chatrooms/${currentProject.chatroom_id}/messages`, { content });
      addLog('api', `[API] Message sent... 200 OK`);
    } catch (error) {
      addLog('error', 'Failed to send message');
    } finally {
      setLoading(false);
    }
  };

  const connectWebSocket = (chatroomId: number) => {
    if (wsRef.current) {
      const oldWs = wsRef.current;
      wsRef.current = null;
      try {
        if (oldWs.readyState === WebSocket.OPEN || oldWs.readyState === WebSocket.CONNECTING) {
          oldWs.close();
        }
      } catch (e) {}
    }

    setTimeout(() => {
      try {
        const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws`);
        wsRef.current = ws;
        ws.onopen = () => {
          addLog('success', 'WebSocket connected');
          try { ws.send(JSON.stringify({ type: 'join', chatroom_id: chatroomId })); } catch (e) {}
        };
        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'message') setMessages(prev => [...prev, data.message]);
            else if (data.type === 'agent_status') updateAgentStatus(data.agent_name, data.status);
          } catch (e) {}
        };
        ws.onclose = () => addLog('warning', 'WebSocket disconnected');
        ws.onerror = () => addLog('error', 'WebSocket error');
      } catch (e) {
        addLog('error', 'Failed to create WebSocket');
      }
    }, 100);
  };

  const updateAgentStatus = (agentName: string, status: Agent['status']) => {
    setAgents(prev => prev.map(a => a.name === agentName ? { ...a, status } : a));
  };

  // ==================== Helpers ====================
  const scrollToBottom = () => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); };
  
  const addLog = (type: LogEntry['type'], message: string) => {
    setLogs(prev => [...prev.slice(-50), { type, message, timestamp: new Date() }]);
  };

  const getStatusIcon = (status?: Agent['status']) => {
    switch (status) {
      case 'thinking': return 'fa-spinner fa-spin';
      case 'executing': return 'fa-circle';
      default: return 'fa-moon';
    }
  };

  const getStatusClass = (status?: Agent['status']) => {
    switch (status) {
      case 'thinking': return 'agent-status-thinking';
      case 'executing': return 'agent-status-executing';
      default: return 'agent-status-idle';
    }
  };

  const getLogColor = (type: LogEntry['type']) => {
    switch (type) {
      case 'success': return 'text-green-400';
      case 'warning': return 'text-yellow-500';
      case 'error': return 'text-red-400';
      case 'api': return 'text-gray-400';
      case 'info': return 'text-blue-400';
      default: return 'text-gray-500';
    }
  };

  const activeAgents = agents.filter(a => a.is_active);

  // ==================== Render ====================
  return (
    <div className="h-screen overflow-hidden flex font-sans bg-dark-900 text-gray-200">
      {/* Sidebar */}
      <aside 
        id="sidebar" 
        className={`fixed inset-y-0 left-0 w-64 md:relative md:flex flex-col border-r border-dark-700 bg-dark-800 transition-transform duration-300 z-30 ${sidebarHidden ? 'mobile-hidden' : ''}`}
      >
        {/* Logo */}
        <div className="h-16 flex items-center justify-between px-6 border-b border-dark-700">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent-400 to-accent-500 flex items-center justify-center text-white font-bold text-xl">
              <i className="fa-solid fa-layer-group"></i>
            </div>
            <span className="text-xl font-semibold tracking-wide text-white">Catown</span>
          </div>
          <button onClick={() => setSidebarHidden(true)} className="md:hidden text-gray-400">
            <i className="fa-solid fa-xmark text-xl"></i>
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto p-4 space-y-6">
          {/* Search */}
          <div className="relative">
            <i className="fa-solid fa-search absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-500 text-sm"></i>
            <input type="text" placeholder="Search rooms..." className="w-full bg-dark-900 border border-dark-700 rounded-lg py-2 pl-9 pr-4 text-sm focus:outline-none focus:border-accent-500 text-gray-300 placeholder-gray-500 transition-colors" />
          </div>

          {/* Main Links */}
          <div className="space-y-1">
            <a href="#" className="flex items-center gap-3 px-3 py-2 rounded-lg bg-dark-700 text-white font-medium">
              <i className="fa-solid fa-house w-5 text-center"></i> Home
            </a>
            <a href="#" className="flex items-center gap-3 px-3 py-2 rounded-lg text-gray-400 hover:bg-dark-700 hover:text-white transition-colors">
              <i className="fa-solid fa-compass w-5 text-center"></i> Explore Agents
            </a>
            <a href="#" className="flex items-center gap-3 px-3 py-2 rounded-lg text-gray-400 hover:bg-dark-700 hover:text-white transition-colors">
              <i className="fa-solid fa-book w-5 text-center"></i> Templates
            </a>
          </div>

          {/* Active Rooms */}
          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 px-3">Active Rooms</h3>
            <div className="space-y-1">
              {projects.map(project => (
                <a 
                  key={project.id}
                  href="#"
                  onClick={(e) => { e.preventDefault(); setCurrentProject(project); }}
                  className={`flex items-center justify-between px-3 py-2 rounded-lg group ${currentProject?.id === project.id ? 'bg-dark-700 text-gray-300' : 'text-gray-400 hover:bg-dark-700 hover:text-gray-300 transition-colors'}`}
                >
                  <div className="flex items-center gap-3 truncate">
                    <span className={`w-2 h-2 rounded-full ${project.status === 'active' ? 'bg-green-500' : 'bg-gray-500'}`}></span>
                    <span className="truncate text-sm">{project.name}</span>
                  </div>
                  <span className={`text-xs px-1.5 py-0.5 rounded ${currentProject?.id === project.id ? 'bg-dark-600 text-gray-400' : 'bg-dark-800 group-hover:bg-dark-600 text-gray-500'}`}>
                    {project.agents.length} AI
                  </span>
                </a>
              ))}
              {projects.length === 0 && (
                <p className="text-xs text-gray-500 px-3 py-2">No rooms yet. Create one!</p>
              )}
            </div>
          </div>
        </nav>

        {/* User Profile */}
        <div className="p-4 border-t border-dark-700">
          <div className="flex items-center gap-3 cursor-pointer hover:bg-dark-700 p-2 rounded-lg transition-colors">
            <div className="w-10 h-10 rounded-full border border-dark-600 bg-gradient-to-br from-accent-400 to-purple-500 flex items-center justify-center text-white font-bold">
              U
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-white truncate">User</p>
              <p className="text-xs text-gray-500 truncate">Pro Plan</p>
            </div>
            <i className="fa-solid fa-chevron-up text-gray-500 text-xs"></i>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col relative overflow-hidden">
        {/* Overlay for background */}
        <div className="absolute inset-0 bg-dark-900/95 z-0"></div>

        {/* Header */}
        <header className="h-16 flex items-center justify-between px-4 md:px-6 border-b border-dark-700/50 z-10 glass-panel">
          <div className="flex items-center gap-3 md:gap-4">
            <button onClick={() => setSidebarHidden(!sidebarHidden)} className="md:hidden text-gray-400 hover:text-white p-2">
              <i className="fa-solid fa-bars text-xl"></i>
            </button>
            <div className="flex items-center gap-2">
              <h1 className="text-base md:text-lg font-semibold text-white truncate max-w-[120px] md:max-w-none">
                {currentProject?.name || 'Catown'}
              </h1>
              {currentProject && (
                <span className="hidden sm:inline-block px-2 py-0.5 rounded text-xs font-medium bg-dark-700 text-gray-300 border border-dark-600">Multi-Agent</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 md:gap-4">
            <button className="hidden sm:block text-gray-400 hover:text-white transition-colors" title="View Logs">
              <i className="fa-solid fa-terminal"></i>
            </button>
            <button onClick={() => setSidePanelHidden(!sidePanelHidden)} className="lg:hidden text-gray-400 hover:text-white p-2">
              <i className="fa-solid fa-receipt"></i>
            </button>
            <button className="bg-accent-500 hover:bg-accent-400 text-white px-3 md:px-4 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-2">
              <i className="fa-solid fa-plus"></i> <span className="hidden sm:inline">Add Agent</span>
            </button>
          </div>
        </header>

        {/* Chat Area & Side Panel Container */}
        <div className="flex-1 flex overflow-hidden z-10">
          
          {/* Chat Window */}
          <div className="flex-1 flex flex-col relative w-full">
            
            {/* Agent Status Bar */}
            <div className="px-4 md:px-6 py-3 border-b border-dark-700/50 flex gap-3 md:gap-4 overflow-x-auto glass-panel no-scrollbar">
              {activeAgents.map(agent => (
                <div key={agent.id} className="flex-shrink-0 flex items-center gap-2 bg-dark-800/80 px-3 py-1.5 rounded-full border border-dark-600">
                  <div className="relative">
                    <div className="w-6 h-6 rounded-full bg-gradient-to-br from-accent-400 to-purple-500 flex items-center justify-center text-white text-xs font-bold">
                      {agent.name.charAt(0).toUpperCase()}
                    </div>
                    <span className="absolute -bottom-1 -right-1 w-3 h-3 bg-dark-800 rounded-full flex items-center justify-center">
                      <i className={`fa-solid ${getStatusIcon(agent.status)} text-[8px] ${getStatusClass(agent.status)}`}></i>
                    </span>
                  </div>
                  <span className="text-sm font-medium text-gray-300">{agent.name}</span>
                  <span className="text-xs text-gray-500 border-l border-dark-600 pl-2 capitalize">{agent.status || 'Idle'}</span>
                </div>
              ))}
            </div>

            {/* Messages Area */}
            <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6">
              {/* Intro Message */}
              {messages.length === 0 && (
                <div className="text-center py-6 md:py-8">
                  <div className="w-16 h-16 md:w-20 md:h-20 mx-auto rounded-full bg-gradient-to-tr from-purple-500 via-accent-500 to-emerald-500 p-1 mb-4 shadow-lg shadow-accent-500/20">
                    <div className="w-full h-full bg-dark-900 rounded-full flex items-center justify-center">
                      <i className="fa-solid fa-robot text-3xl text-white"></i>
                    </div>
                  </div>
                  <h2 className="text-xl md:text-2xl font-bold text-white mb-2">
                    {currentProject ? `${currentProject.name} Initialized` : 'Welcome to Catown'}
                  </h2>
                  <p className="text-sm md:text-base text-gray-400 max-w-md mx-auto px-4">
                    {currentProject 
                      ? `I've assembled ${activeAgents.map(a => a.name).join(', ')}. What project are we working on today?`
                      : 'Multi-Agent Collaboration Platform - Create a room to begin'}
                  </p>
                </div>
              )}

              {/* Messages */}
              {messages.map(message => (
                message.message_type === 'user' || !message.agent_name ? (
                  // User Message
                  <div key={message.id} className="flex gap-3 md:gap-4 justify-end">
                    <div className="bg-accent-600 text-white px-4 md:px-5 py-2 md:py-3 rounded-2xl rounded-tr-sm max-w-[85%] md:max-w-[80%] shadow-md text-sm md:text-base">
                      <p>{message.content}</p>
                    </div>
                    <div className="w-8 h-8 rounded-full mt-auto bg-gradient-to-br from-accent-400 to-purple-500 flex items-center justify-center text-white font-bold text-sm">U</div>
                  </div>
                ) : (
                  // Agent Message
                  <div key={message.id} className="flex gap-4">
                    <div className="relative mt-auto">
                      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-emerald-400 to-accent-500 flex items-center justify-center text-white font-bold text-sm">
                        {message.agent_name.charAt(0).toUpperCase()}
                      </div>
                      <span className="absolute -top-1 -right-1 w-3 h-3 bg-dark-900 rounded-full flex items-center justify-center">
                        <i className="fa-solid fa-circle text-[8px] agent-status-executing"></i>
                      </span>
                    </div>
                    <div className="bg-dark-700/80 border border-dark-600 text-gray-200 px-5 py-4 rounded-2xl rounded-tl-sm max-w-[80%] shadow-md">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="font-semibold text-sm text-emerald-400">{message.agent_name}</span>
                        <span className="text-xs text-gray-500">{message.created_at ? format(new Date(message.created_at), 'HH:mm') : 'Just now'}</span>
                      </div>
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    </div>
                  </div>
                )
              ))}
              <div ref={messagesEndRef} />
            </div>

            {/* Input Area */}
            <div className="p-4 bg-dark-900/80 border-t border-dark-700/50 backdrop-blur-md">
              <div className="max-w-4xl mx-auto">
                {/* Command Hints */}
                <div className="flex gap-2 mb-2 px-2">
                  <button onClick={() => setInput(prev => prev + '@')} className="text-xs bg-dark-700 hover:bg-dark-600 text-gray-300 px-2 py-1 rounded border border-dark-600 transition-colors">
                    <i className="fa-solid fa-at text-gray-400 mr-1"></i> Mention Agent
                  </button>
                  <button onClick={() => setInput('创建项目：')} className="text-xs bg-dark-700 hover:bg-dark-600 text-gray-300 px-2 py-1 rounded border border-dark-600 transition-colors">
                    <i className="fa-solid fa-plus text-gray-400 mr-1"></i> Create Room
                  </button>
                  <button className="text-xs bg-dark-700 hover:bg-dark-600 text-gray-300 px-2 py-1 rounded border border-dark-600 transition-colors">
                    <i className="fa-solid fa-terminal text-gray-400 mr-1"></i> Run Script
                  </button>
                </div>
                
                {/* Input Box */}
                <div className="relative flex items-end gap-2 bg-dark-800 border border-dark-600 rounded-xl p-2 shadow-lg focus-within:border-accent-500 focus-within:ring-1 focus-within:ring-accent-500 transition-all">
                  <button className="p-2 text-gray-400 hover:text-white transition-colors rounded-lg hover:bg-dark-700">
                    <i className="fa-solid fa-paperclip"></i>
                  </button>
                  <textarea 
                    rows={1}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }}}
                    className="w-full bg-transparent text-gray-200 placeholder-gray-500 resize-none outline-none py-2 max-h-32"
                    placeholder="Message room or @agent... Try 'Create a new room for SEO analysis'"
                  />
                  <div className="flex gap-1">
                    <button className="p-2 text-gray-400 hover:text-white transition-colors rounded-lg hover:bg-dark-700">
                      <i className="fa-solid fa-microphone"></i>
                    </button>
                    <button onClick={sendMessage} disabled={loading || !input.trim()} className="p-2 bg-accent-500 hover:bg-accent-400 disabled:bg-dark-600 text-white disabled:text-gray-500 transition-colors rounded-lg shadow-md">
                      <i className="fa-solid fa-paper-plane"></i>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Side Panel (Logs & Config) */}
          <div className={`w-80 border-l border-dark-700/50 bg-dark-900/95 backdrop-blur-xl flex flex-col ${sidePanelHidden ? 'hidden' : 'hidden lg:flex'}`}>
            {/* Panel Tabs */}
            <div className="flex border-b border-dark-700">
              <button className="flex-1 py-3 text-sm font-medium text-white border-b-2 border-accent-500 bg-dark-800/50">
                <i className="fa-solid fa-terminal mr-2"></i>Logs
              </button>
              <button className="flex-1 py-3 text-sm font-medium text-gray-400 hover:text-white hover:bg-dark-800/30 transition-colors">
                <i className="fa-solid fa-sliders mr-2"></i>Config
              </button>
            </div>

            {/* Log Content */}
            <div className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-2">
              {logs.map((log, i) => (
                <div key={i} className={getLogColor(log.type)}>{log.message}</div>
              ))}
              {/* Live terminal effect */}
              <div className="flex items-center gap-2 mt-4 text-accent-400">
                <span>&gt;</span>
                <span className="animate-pulse">_</span>
              </div>
            </div>

            {/* System Status */}
            <div className="p-4 border-t border-dark-700 bg-dark-800/50">
              <h4 className="text-xs font-semibold text-gray-400 uppercase mb-3">System Status</h4>
              <div className="space-y-3">
                <div>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-gray-300">API Usage (Tokens)</span>
                    <span className="text-gray-400">45k / 100k</span>
                  </div>
                  <div className="h-1.5 w-full bg-dark-700 rounded-full overflow-hidden">
                    <div className="h-full bg-blue-500 w-[45%] rounded-full"></div>
                  </div>
                </div>
                <div className="flex justify-between items-center text-xs">
                  <span className="text-gray-300">Backend Latency</span>
                  <span className="text-green-400 flex items-center gap-1"><i className="fa-solid fa-circle text-[8px]"></i> 124ms</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
