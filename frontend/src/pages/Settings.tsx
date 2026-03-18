import { useState, useEffect } from 'react';
import { Save, RefreshCw, CheckCircle, AlertCircle, Eye, EyeOff } from 'lucide-react';
import axios from 'axios';

interface LLMConfig {
  api_key: string;
  base_url: string;
  model: string;
  temperature: number;
  max_tokens: number;
}

export default function Settings() {
  const [config, setConfig] = useState<LLMConfig>({
    api_key: '',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4',
    temperature: 0.7,
    max_tokens: 2000
  });
  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);

  useEffect(() => {
    fetchConfig();
  }, []);

  const fetchConfig = async () => {
    try {
      const response = await axios.get('/api/config');
      setConfig(response.data);
    } catch (err) {
      console.log('No config found, using defaults');
    }
  };

  const handleSave = async () => {
    setLoading(true);
    setError('');
    setSaved(false);
    
    try {
      await axios.post('/api/config', config);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save configuration');
    } finally {
      setLoading(false);
    }
  };

  const handleTestConnection = async () => {
    setLoading(true);
    setError('');
    
    try {
      await axios.post('/api/config/test', config);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Connection test failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-6">
          <div className="flex items-center space-x-4">
            <div className="text-4xl">⚙️</div>
            <div>
              <h1 className="text-3xl font-bold text-slate-900">Settings</h1>
              <p className="text-slate-600 mt-1">Configure LLM API settings for your agents</p>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-6xl mx-auto px-6 py-8">
        <div className="bg-white rounded-2xl shadow-lg border border-slate-200 overflow-hidden">
          {/* Section Header */}
          <div className="bg-gradient-to-r from-primary-600 to-primary-700 px-8 py-6">
            <h2 className="text-2xl font-semibold text-white flex items-center">
              <span className="mr-3">🤖</span>
              LLM Configuration
            </h2>
            <p className="text-primary-100 mt-2">
              Configure the Language Model API for agent interactions
            </p>
          </div>

          {/* Form */}
          <div className="p-8 space-y-6">
            {/* API Key */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-2">
                API Key <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <input
                  type={showApiKey ? "text" : "password"}
                  value={config.api_key}
                  onChange={(e) => setConfig({ ...config, api_key: e.target.value })}
                  className="w-full px-4 py-3 border-2 border-slate-200 rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all text-slate-800 font-mono text-sm"
                  placeholder="sk-..."
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(!showApiKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  {showApiKey ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Your API key is stored securely and never shared
              </p>
            </div>

            {/* Base URL */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-2">
                API Base URL
              </label>
              <input
                type="text"
                value={config.base_url}
                onChange={(e) => setConfig({ ...config, base_url: e.target.value })}
                className="w-full px-4 py-3 border-2 border-slate-200 rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all text-slate-800"
                placeholder="https://api.openai.com/v1"
              />
              <p className="text-xs text-slate-500 mt-2">
                Supports OpenAI-compatible APIs (OpenAI, Azure, Anthropic, etc.)
              </p>
            </div>

            {/* Model Selection */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-2">
                Model
              </label>
              <select
                value={config.model}
                onChange={(e) => setConfig({ ...config, model: e.target.value })}
                className="w-full px-4 py-3 border-2 border-slate-200 rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all text-slate-800 bg-white"
              >
                <option value="gpt-4">GPT-4</option>
                <option value="gpt-4-turbo-preview">GPT-4 Turbo</option>
                <option value="gpt-3.5-turbo">GPT-3.5 Turbo</option>
                <option value="claude-3-opus-20240229">Claude 3 Opus</option>
                <option value="claude-3-sonnet-20240229">Claude 3 Sonnet</option>
                <option value="custom">Custom Model</option>
              </select>
            </div>

            {/* Temperature & Max Tokens */}
            <div className="grid grid-cols-2 gap-6">
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-2">
                  Temperature: {config.temperature}
                </label>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.1"
                  value={config.temperature}
                  onChange={(e) => setConfig({ ...config, temperature: parseFloat(e.target.value) })}
                  className="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-primary-600"
                />
                <div className="flex justify-between text-xs text-slate-500 mt-1">
                  <span>Precise</span>
                  <span>Creative</span>
                </div>
              </div>

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-2">
                  Max Tokens
                </label>
                <input
                  type="number"
                  value={config.max_tokens}
                  onChange={(e) => setConfig({ ...config, max_tokens: parseInt(e.target.value) })}
                  className="w-full px-4 py-3 border-2 border-slate-200 rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all text-slate-800"
                />
              </div>
            </div>

            {/* Status Messages */}
            {saved && (
              <div className="flex items-center space-x-2 text-green-700 bg-green-50 border border-green-200 rounded-xl px-4 py-3">
                <CheckCircle className="w-5 h-5" />
                <span className="font-medium">Configuration saved successfully!</span>
              </div>
            )}

            {error && (
              <div className="flex items-center space-x-2 text-red-700 bg-red-50 border border-red-200 rounded-xl px-4 py-3">
                <AlertCircle className="w-5 h-5" />
                <span className="font-medium">{error}</span>
              </div>
            )}

            {/* Action Buttons */}
            <div className="flex space-x-4 pt-4 border-t border-slate-200">
              <button
                onClick={handleSave}
                disabled={loading || !config.api_key}
                className="flex-1 flex items-center justify-center space-x-2 px-6 py-3 bg-primary-600 text-white rounded-xl hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all font-medium shadow-lg hover:shadow-xl"
              >
                {loading ? (
                  <RefreshCw className="w-5 h-5 animate-spin" />
                ) : (
                  <Save className="w-5 h-5" />
                )}
                <span>Save Configuration</span>
              </button>

              <button
                onClick={handleTestConnection}
                disabled={loading || !config.api_key}
                className="px-6 py-3 border-2 border-slate-300 text-slate-700 rounded-xl hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-all font-medium"
              >
                Test Connection
              </button>
            </div>
          </div>
        </div>

        {/* Info Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-8">
          <div className="bg-white rounded-xl shadow border border-slate-200 p-6">
            <h3 className="font-semibold text-slate-900 flex items-center mb-3">
              <span className="text-2xl mr-2">💡</span>
              Supported Providers
            </h3>
            <ul className="space-y-2 text-sm text-slate-600">
              <li className="flex items-center"><span className="w-2 h-2 bg-primary-500 rounded-full mr-2"></span>OpenAI</li>
              <li className="flex items-center"><span className="w-2 h-2 bg-primary-500 rounded-full mr-2"></span>Azure OpenAI</li>
              <li className="flex items-center"><span className="w-2 h-2 bg-primary-500 rounded-full mr-2"></span>Anthropic Claude</li>
              <li className="flex items-center"><span className="w-2 h-2 bg-primary-500 rounded-full mr-2"></span>Local LLMs (Ollama, LM Studio)</li>
            </ul>
          </div>

          <div className="bg-white rounded-xl shadow border border-slate-200 p-6">
            <h3 className="font-semibold text-slate-900 flex items-center mb-3">
              <span className="text-2xl mr-2">🔒</span>
              Security
            </h3>
            <ul className="space-y-2 text-sm text-slate-600">
              <li className="flex items-center"><span className="w-2 h-2 bg-green-500 rounded-full mr-2"></span>API keys are encrypted at rest</li>
              <li className="flex items-center"><span className="w-2 h-2 bg-green-500 rounded-full mr-2"></span>Keys are never logged or exposed</li>
              <li className="flex items-center"><span className="w-2 h-2 bg-green-500 rounded-full mr-2"></span>Local storage only</li>
            </ul>
          </div>
        </div>
      </main>
    </div>
  );
}
