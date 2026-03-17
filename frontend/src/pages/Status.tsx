import { useState, useEffect } from 'react';
import { Activity, Server, Database, Users, CheckCircle, AlertCircle } from 'lucide-react';

interface SystemStatus {
  status: string;
  version: string;
  stats: {
    agents: number;
    projects: number;
    chatrooms: number;
    messages: number;
  };
  features: {
    llm_enabled: boolean;
    websocket_enabled: boolean;
    tools_enabled: boolean;
    memory_enabled: boolean;
  };
}

export default function Status() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchStatus();
  }, []);

  const fetchStatus = async () => {
    try {
      const response = await fetch('/api/status');
      const data = await response.json();
      setStatus(data);
    } catch (error) {
      console.error('Error fetching status:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <AlertCircle className="w-12 h-12 text-red-500 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-gray-900 mb-2">Unable to fetch status</h2>
          <button
            onClick={fetchStatus}
            className="text-primary-600 hover:text-primary-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center space-x-3">
            <Activity className="w-8 h-8 text-primary-600" />
            <h1 className="text-2xl font-bold text-gray-900">System Status</h1>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Status Banner */}
        <div className={`rounded-lg p-4 mb-8 ${
          status.status === 'healthy' 
            ? 'bg-green-50 border border-green-200' 
            : 'bg-red-50 border border-red-200'
        }`}>
          <div className="flex items-center space-x-3">
            {status.status === 'healthy' ? (
              <CheckCircle className="w-6 h-6 text-green-600" />
            ) : (
              <AlertCircle className="w-6 h-6 text-red-600" />
            )}
            <div>
              <h2 className="font-semibold text-gray-900">
                System Status: {status.status.charAt(0).toUpperCase() + status.status.slice(1)}
              </h2>
              <p className="text-sm text-gray-600">Version: {status.version}</p>
            </div>
          </div>
        </div>

        <div className="grid gap-8 md:grid-cols-2">
          {/* Statistics */}
          <div className="bg-white rounded-lg shadow">
            <div className="p-4 border-b flex items-center space-x-2">
              <Server className="w-5 h-5 text-primary-600" />
              <h2 className="text-lg font-semibold text-gray-900">Statistics</h2>
            </div>
            
            <div className="p-6 grid grid-cols-2 gap-4">
              <div className="text-center p-4 bg-blue-50 rounded-lg">
                <Users className="w-8 h-8 text-blue-600 mx-auto mb-2" />
                <p className="text-2xl font-bold text-blue-900">{status.stats.agents}</p>
                <p className="text-sm text-blue-700">Agents</p>
              </div>
              
              <div className="text-center p-4 bg-purple-50 rounded-lg">
                <Database className="w-8 h-8 text-purple-600 mx-auto mb-2" />
                <p className="text-2xl font-bold text-purple-900">{status.stats.projects}</p>
                <p className="text-sm text-purple-700">Projects</p>
              </div>
              
              <div className="text-center p-4 bg-green-50 rounded-lg">
                <Activity className="w-8 h-8 text-green-600 mx-auto mb-2" />
                <p className="text-2xl font-bold text-green-900">{status.stats.chatrooms}</p>
                <p className="text-sm text-green-700">Chatrooms</p>
              </div>
              
              <div className="text-center p-4 bg-yellow-50 rounded-lg">
                <MessageSquareIcon className="w-8 h-8 text-yellow-600 mx-auto mb-2" />
                <p className="text-2xl font-bold text-yellow-900">{status.stats.messages}</p>
                <p className="text-sm text-yellow-700">Messages</p>
              </div>
            </div>
          </div>

          {/* Features */}
          <div className="bg-white rounded-lg shadow">
            <div className="p-4 border-b flex items-center space-x-2">
              <CheckCircle className="w-5 h-5 text-primary-600" />
              <h2 className="text-lg font-semibold text-gray-900">Enabled Features</h2>
            </div>
            
            <div className="p-6 space-y-4">
              <FeatureItem
                name="LLM Integration"
                enabled={status.features.llm_enabled}
                description="OpenAI-compatible AI models"
              />
              
              <FeatureItem
                name="WebSocket Support"
                enabled={status.features.websocket_enabled}
                description="Real-time bidirectional communication"
              />
              
              <FeatureItem
                name="Tool Calling"
                enabled={status.features.tools_enabled}
                description="Agent tool and skill execution"
              />
              
              <FeatureItem
                name="Memory System"
                enabled={status.features.memory_enabled}
                description="Short-term and long-term memory"
              />
            </div>
          </div>
        </div>

        {/* System Information */}
        <div className="mt-8 bg-white rounded-lg shadow">
          <div className="p-4 border-b">
            <h2 className="text-lg font-semibold text-gray-900">System Information</h2>
          </div>
          
          <div className="p-6">
            <table className="w-full">
              <tbody className="divide-y">
                <tr>
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">Platform</td>
                  <td className="py-3 px-4 text-sm text-gray-600">Catown Multi-Agent System</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">Version</td>
                  <td className="py-3 px-4 text-sm text-gray-600">{status.version}</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">Backend</td>
                  <td className="py-3 px-4 text-sm text-gray-600">FastAPI + Python</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">Frontend</td>
                  <td className="py-3 px-4 text-sm text-gray-600">React + TypeScript + TailwindCSS</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">Database</td>
                  <td className="py-3 px-4 text-sm text-gray-600">SQLite (scalable to PostgreSQL)</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">Communication</td>
                  <td className="py-3 px-4 text-sm text-gray-600">WebSocket + REST API</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          <a
            href="/"
            className="bg-white rounded-lg shadow p-6 hover:shadow-lg transition-shadow text-center"
          >
            <div className="text-3xl mb-2">🏠</div>
            <h3 className="font-semibold text-gray-900 mb-1">Dashboard</h3>
            <p className="text-sm text-gray-600">Manage your projects</p>
          </a>
          
          <a
            href="/agents"
            className="bg-white rounded-lg shadow p-6 hover:shadow-lg transition-shadow text-center"
          >
            <div className="text-3xl mb-2">🤖</div>
            <h3 className="font-semibold text-gray-900 mb-1">Agents</h3>
            <p className="text-sm text-gray-600">View agent details</p>
          </a>
          
          <a
            href="/docs"
            className="bg-white rounded-lg shadow p-6 hover:shadow-lg transition-shadow text-center"
          >
            <div className="text-3xl mb-2">📚</div>
            <h3 className="font-semibold text-gray-900 mb-1">API Docs</h3>
            <p className="text-sm text-gray-600">Developer documentation</p>
          </a>
        </div>
      </main>
    </div>
  );
}

function FeatureItem({ name, enabled, description }: { 
  name: string;
  enabled: boolean;
  description: string;
}) {
  return (
    <div className="flex items-start space-x-3">
      {enabled ? (
        <CheckCircle className="w-5 h-5 text-green-500 mt-0.5" />
      ) : (
        <AlertCircle className="w-5 h-5 text-red-500 mt-0.5" />
      )}
      <div>
        <h3 className="font-medium text-gray-900">{name}</h3>
        <p className="text-sm text-gray-600">{description}</p>
      </div>
    </div>
  );
}

function MessageSquareIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
      />
    </svg>
  );
}
