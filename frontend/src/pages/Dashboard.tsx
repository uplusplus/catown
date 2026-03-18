import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus, MessageSquare, Users, Activity, Settings, Sparkles, Zap, Shield } from 'lucide-react';
import axios from 'axios';

interface Project {
  id: number;
  name: string;
  description: string;
  status: string;
  chatroom_id: number | null;
  agents: Array<{
    id: number;
    name: string;
    role: string;
    is_active: boolean;
  }>;
}

interface CreateProjectData {
  name: string;
  description: string;
  agent_names: string[];
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newProject, setNewProject] = useState<CreateProjectData>({
    name: '',
    description: '',
    agent_names: ['assistant']
  });

  useEffect(() => {
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const response = await axios.get('/api/projects');
      setProjects(response.data);
    } catch (error) {
      console.error('Error fetching projects:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateProject = async () => {
    try {
      await axios.post('/api/projects', newProject);
      setShowCreateModal(false);
      setNewProject({ name: '', description: '', agent_names: ['assistant'] });
      fetchProjects();
    } catch (error) {
      console.error('Error creating project:', error);
      alert('Failed to create project');
    }
  };

  const agentOptions = [
    { name: 'assistant', role: 'General Assistant', icon: '🤖', color: 'bg-blue-500' },
    { name: 'coder', role: 'Code Expert', icon: '💻', color: 'bg-purple-500' },
    { name: 'reviewer', role: 'Review Specialist', icon: '👀', color: 'bg-orange-500' },
    { name: 'researcher', role: 'Research Expert', icon: '🔬', color: 'bg-green-500' }
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-lg shadow-sm border-b border-slate-200/50 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <div className="text-4xl animate-bounce">🐱</div>
              <div>
                <h1 className="text-2xl font-bold bg-gradient-to-r from-primary-600 to-indigo-600 bg-clip-text text-transparent">
                  Catown
                </h1>
                <p className="text-xs text-slate-500">Multi-Agent Platform</p>
              </div>
            </div>
            <nav className="flex items-center space-x-2">
              <Link 
                to="/agents" 
                className="flex items-center space-x-2 px-4 py-2 text-slate-600 hover:text-primary-600 hover:bg-primary-50 rounded-lg transition-all"
              >
                <Users className="w-5 h-5" />
                <span className="font-medium">Agents</span>
              </Link>
              <Link 
                to="/status" 
                className="flex items-center space-x-2 px-4 py-2 text-slate-600 hover:text-primary-600 hover:bg-primary-50 rounded-lg transition-all"
              >
                <Activity className="w-5 h-5" />
                <span className="font-medium">Status</span>
              </Link>
              <Link 
                to="/settings" 
                className="flex items-center space-x-2 px-4 py-2 text-slate-600 hover:text-primary-600 hover:bg-primary-50 rounded-lg transition-all"
              >
                <Settings className="w-5 h-5" />
                <span className="font-medium">Settings</span>
              </Link>
            </nav>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-6 py-8">
        {/* Welcome Section */}
        <div className="mb-8">
          <div className="bg-gradient-to-r from-primary-600 via-indigo-600 to-purple-600 rounded-2xl p-8 text-white shadow-2xl">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-4xl font-bold mb-3 flex items-center">
                  <Sparkles className="w-10 h-10 mr-3" />
                  Welcome to Catown!
                </h2>
                <p className="text-primary-100 text-lg max-w-2xl">
                  Create projects and let AI agents work together to accomplish complex tasks.
                  Each agent has unique skills and can collaborate in real-time.
                </p>
              </div>
              <div className="text-8xl opacity-20">🚀</div>
            </div>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-3 gap-6 mb-8">
          <div className="bg-white rounded-xl shadow border border-slate-200 p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500">Active Projects</p>
                <p className="text-3xl font-bold text-slate-900 mt-1">{projects.length}</p>
              </div>
              <div className="w-12 h-12 bg-primary-100 rounded-lg flex items-center justify-center">
                <MessageSquare className="w-6 h-6 text-primary-600" />
              </div>
            </div>
          </div>
          
          <div className="bg-white rounded-xl shadow border border-slate-200 p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500">Active Agents</p>
                <p className="text-3xl font-bold text-slate-900 mt-1">4</p>
              </div>
              <div className="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center">
                <Users className="w-6 h-6 text-purple-600" />
              </div>
            </div>
          </div>
          
          <div className="bg-white rounded-xl shadow border border-slate-200 p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500">Status</p>
                <p className="text-3xl font-bold text-green-600 mt-1">Online</p>
              </div>
              <div className="w-12 h-12 bg-green-100 rounded-lg flex items-center justify-center">
                <Zap className="w-6 h-6 text-green-600" />
              </div>
            </div>
          </div>
        </div>

        {/* Create Project Button */}
        <div className="mb-6">
          <button
            onClick={() => setShowCreateModal(true)}
            className="inline-flex items-center px-6 py-3 bg-gradient-to-r from-primary-600 to-indigo-600 text-white rounded-xl hover:from-primary-700 hover:to-indigo-700 transition-all font-medium shadow-lg hover:shadow-xl transform hover:-translate-y-0.5"
          >
            <Plus className="w-5 h-5 mr-2" />
            Create New Project
          </button>
        </div>

        {/* Projects Grid */}
        {loading ? (
          <div className="text-center py-16">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-600 border-t-transparent"></div>
            <p className="text-slate-500 mt-4">Loading projects...</p>
          </div>
        ) : projects.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-2xl shadow border border-slate-200">
            <div className="text-6xl mb-4">📝</div>
            <h3 className="text-xl font-semibold text-slate-900 mb-2">No projects yet</h3>
            <p className="text-slate-600 mb-6">Create your first project to get started with multi-agent collaboration</p>
            <button
              onClick={() => setShowCreateModal(true)}
              className="inline-flex items-center px-6 py-3 bg-primary-600 text-white rounded-xl hover:bg-primary-700 transition-all font-medium"
            >
              <Plus className="w-5 h-5 mr-2" />
              Create Project
            </button>
          </div>
        ) : (
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {projects.map((project) => (
              <div 
                key={project.id} 
                className="bg-white rounded-xl shadow border border-slate-200 hover:shadow-xl transition-all transform hover:-translate-y-1 overflow-hidden"
              >
                <div className="p-6">
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex-1">
                      <h3 className="text-xl font-semibold text-slate-900 mb-1">{project.name}</h3>
                      <div className="flex items-center space-x-2">
                        <span className={`px-3 py-1 text-xs font-medium rounded-full ${
                          project.status === 'active' 
                            ? 'bg-green-100 text-green-700 border border-green-200' 
                            : 'bg-slate-100 text-slate-700 border border-slate-200'
                        }`}>
                          {project.status}
                        </span>
                      </div>
                    </div>
                  </div>
                  
                  <p className="text-slate-600 mb-4 text-sm line-clamp-2">{project.description}</p>
                  
                  <div className="mb-4 pt-4 border-t border-slate-100">
                    <p className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wider">
                      Agents ({project.agents.length})
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {project.agents.map((agent) => {
                        const agentInfo = agentOptions.find(a => a.name === agent.name);
                        return (
                          <span 
                            key={agent.id} 
                            className={`${agentInfo?.color || 'bg-slate-500'} text-white px-3 py-1 text-xs font-medium rounded-full flex items-center space-x-1`}
                          >
                            <span>{agentInfo?.icon || '🤖'}</span>
                            <span>{agent.name}</span>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                  
                  <div className="flex space-x-3 pt-4 border-t border-slate-100">
                    {project.chatroom_id && (
                      <Link
                        to={`/chat/${project.chatroom_id}`}
                        className="flex-1 flex items-center justify-center space-x-2 px-4 py-2.5 bg-gradient-to-r from-primary-600 to-indigo-600 text-white rounded-lg hover:from-primary-700 hover:to-indigo-700 transition-all font-medium"
                      >
                        <MessageSquare className="w-4 h-4" />
                        <span>Chat</span>
                      </Link>
                    )}
                    <Link
                      to={`/projects/${project.id}`}
                      className="flex-1 flex items-center justify-center space-x-2 px-4 py-2.5 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 transition-all font-medium"
                    >
                      <span>Details</span>
                    </Link>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* Create Project Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
            <div className="bg-gradient-to-r from-primary-600 to-indigo-600 px-6 py-4">
              <h2 className="text-xl font-semibold text-white flex items-center">
                <Plus className="w-5 h-5 mr-2" />
                Create New Project
              </h2>
            </div>
            
            <div className="p-6 space-y-5">
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-2">
                  Project Name <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={newProject.name}
                  onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
                  className="w-full px-4 py-3 border-2 border-slate-200 rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all"
                  placeholder="My Awesome Project"
                />
              </div>
              
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-2">
                  Description
                </label>
                <textarea
                  value={newProject.description}
                  onChange={(e) => setNewProject({ ...newProject, description: e.target.value })}
                  className="w-full px-4 py-3 border-2 border-slate-200 rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all resize-none"
                  rows={3}
                  placeholder="Project description..."
                />
              </div>
              
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-3">
                  Select Agents
                </label>
                <div className="grid grid-cols-2 gap-3">
                  {agentOptions.map((agent) => (
                    <label 
                      key={agent.name} 
                      className={`flex items-center space-x-3 p-3 border-2 rounded-xl cursor-pointer transition-all ${
                        newProject.agent_names.includes(agent.name)
                          ? 'border-primary-500 bg-primary-50'
                          : 'border-slate-200 hover:border-slate-300'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={newProject.agent_names.includes(agent.name)}
                        onChange={(e) => {
                          const names = e.target.checked
                            ? [...newProject.agent_names, agent.name]
                            : newProject.agent_names.filter(n => n !== agent.name);
                          setNewProject({ ...newProject, agent_names: names });
                        }}
                        className="w-4 h-4 text-primary-600 rounded focus:ring-primary-500"
                      />
                      <span className="text-2xl">{agent.icon}</span>
                      <div className="flex-1">
                        <span className="font-medium text-slate-900 capitalize block">{agent.name}</span>
                        <span className="text-xs text-slate-500">{agent.role}</span>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            </div>
            
            <div className="flex space-x-3 px-6 pb-6">
              <button
                onClick={() => setShowCreateModal(false)}
                className="flex-1 px-4 py-3 bg-slate-100 text-slate-700 rounded-xl hover:bg-slate-200 transition-all font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateProject}
                disabled={!newProject.name}
                className="flex-1 px-4 py-3 bg-gradient-to-r from-primary-600 to-indigo-600 text-white rounded-xl hover:from-primary-700 hover:to-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all font-medium"
              >
                Create Project
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
