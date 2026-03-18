import { useState, useEffect } from 'react';
import { Brain, Database, Clock, MessageSquare } from 'lucide-react';

interface Agent {
  id: number;
  name: string;
  role: string;
  is_active: boolean;
}

interface MemoryItem {
  id: number;
  type: string;
  content: string;
  importance: number;
  created_at: string;
}

interface AgentMemory {
  agent_name: string;
  memory_count: number;
  memories: MemoryItem[];
}

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<number | null>(null);
  const [agentMemory, setAgentMemory] = useState<AgentMemory | null>(null);
  const [loading, setLoading] = useState(true);
  const [memoryLoading, setMemoryLoading] = useState(false);

  useEffect(() => {
    fetchAgents();
  }, []);

  const fetchAgents = async () => {
    try {
      const response = await fetch('/api/agents');
      const data = await response.json();
      setAgents(data);
    } catch (error) {
      console.error('Error fetching agents:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchAgentMemory = async (agentId: number) => {
    setMemoryLoading(true);
    try {
      const response = await fetch(`/api/agents/${agentId}/memory`);
      const data = await response.json();
      setAgentMemory(data);
    } catch (error) {
      console.error('Error fetching agent memory:', error);
    } finally {
      setMemoryLoading(false);
    }
  };

  const handleAgentClick = (agentId: number) => {
    setSelectedAgent(agentId);
    fetchAgentMemory(agentId);
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center space-x-3">
            <div className="text-3xl">🤖</div>
            <h1 className="text-2xl font-bold text-gray-900">AI Agents</h1>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="grid gap-8 lg:grid-cols-3">
          {/* Agent List */}
          <div className="lg:col-span-1">
            <div className="bg-white rounded-lg shadow">
              <div className="p-4 border-b">
                <h2 className="text-lg font-semibold text-gray-900">Available Agents</h2>
              </div>
              
              {loading ? (
                <div className="p-8 text-center">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 mx-auto"></div>
                </div>
              ) : (
                <div className="divide-y">
                  {agents.map((agent) => (
                    <button
                      key={agent.id}
                      onClick={() => handleAgentClick(agent.id)}
                      className={`w-full p-4 text-left hover:bg-gray-50 transition-colors ${
                        selectedAgent === agent.id ? 'bg-blue-50 border-l-4 border-primary-600' : ''
                      }`}
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <h3 className="font-medium text-gray-900 capitalize">{agent.name}</h3>
                          <p className="text-sm text-gray-600 mt-1">{agent.role}</p>
                        </div>
                        {agent.is_active && (
                          <span className="px-2 py-1 bg-green-100 text-green-800 text-xs rounded">
                            Active
                          </span>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Agent Details */}
          <div className="lg:col-span-2">
            {selectedAgent && agentMemory ? (
              <div className="space-y-6">
                {/* Agent Info */}
                <div className="bg-white rounded-lg shadow p-6">
                  <h2 className="text-2xl font-bold text-gray-900 mb-2 capitalize">
                    {agentMemory.agent_name}
                  </h2>
                  <p className="text-gray-600 mb-4">
                    Total Memories: {agentMemory.memory_count}
                  </p>
                  
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="bg-blue-50 rounded-lg p-4">
                      <div className="flex items-center space-x-2 mb-1">
                        <Brain className="w-5 h-5 text-blue-600" />
                        <span className="text-sm font-medium text-blue-900">Short-term</span>
                      </div>
                      <p className="text-2xl font-bold text-blue-600">
                        {agentMemory.memories.filter(m => m.type === 'short_term').length}
                      </p>
                    </div>
                    
                    <div className="bg-purple-50 rounded-lg p-4">
                      <div className="flex items-center space-x-2 mb-1">
                        <Database className="w-5 h-5 text-purple-600" />
                        <span className="text-sm font-medium text-purple-900">Long-term</span>
                      </div>
                      <p className="text-2xl font-bold text-purple-600">
                        {agentMemory.memories.filter(m => m.type === 'long_term').length}
                      </p>
                    </div>
                    
                    <div className="bg-green-50 rounded-lg p-4">
                      <div className="flex items-center space-x-2 mb-1">
                        <MessageSquare className="w-5 h-5 text-green-600" />
                        <span className="text-sm font-medium text-green-900">Messages</span>
                      </div>
                      <p className="text-2xl font-bold text-green-600">
                        {agentMemory.memories.length}
                      </p>
                    </div>
                    
                    <div className="bg-yellow-50 rounded-lg p-4">
                      <div className="flex items-center space-x-2 mb-1">
                        <Clock className="w-5 h-5 text-yellow-600" />
                        <span className="text-sm font-medium text-yellow-900">Avg Importance</span>
                      </div>
                      <p className="text-2xl font-bold text-yellow-600">
                        {agentMemory.memories.length > 0
                          ? (agentMemory.memories.reduce((sum, m) => sum + m.importance, 0) / agentMemory.memories.length).toFixed(1)
                          : '0.0'}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Memory List */}
                <div className="bg-white rounded-lg shadow">
                  <div className="p-4 border-b flex items-center justify-between">
                    <h3 className="text-lg font-semibold text-gray-900">Memory Contents</h3>
                    {memoryLoading && (
                      <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-primary-600"></div>
                    )}
                  </div>
                  
                  {agentMemory.memories.length === 0 ? (
                    <div className="p-8 text-center text-gray-600">
                      No memories yet
                    </div>
                  ) : (
                    <div className="divide-y max-h-96 overflow-y-auto">
                      {agentMemory.memories.map((memory) => (
                        <div key={memory.id} className="p-4 hover:bg-gray-50">
                          <div className="flex items-start justify-between mb-2">
                            <span className={`px-2 py-1 text-xs rounded capitalize ${
                              memory.type === 'short_term' ? 'bg-blue-100 text-blue-800' :
                              memory.type === 'long_term' ? 'bg-purple-100 text-purple-800' :
                              'bg-gray-100 text-gray-800'
                            }`}>
                              {memory.type.replace('_', ' ')}
                            </span>
                            <div className="flex items-center space-x-1">
                              <span className="text-xs text-gray-500">Importance:</span>
                              <div className="flex space-x-0.5">
                                {[...Array(10)].map((_, i) => (
                                  <div
                                    key={i}
                                    className={`w-1 h-3 rounded ${
                                      i < memory.importance ? 'bg-yellow-500' : 'bg-gray-200'
                                    }`}
                                  />
                                ))}
                              </div>
                            </div>
                          </div>
                          
                          <p className="text-gray-700 text-sm mb-2">{memory.content}</p>
                          
                          <p className="text-xs text-gray-500">
                            {new Date(memory.created_at).toLocaleString()}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="bg-white rounded-lg shadow p-12 text-center">
                <Brain className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-gray-900 mb-2">Select an Agent</h3>
                <p className="text-gray-600">
                  Click on an agent from the list to view its details and memory
                </p>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
