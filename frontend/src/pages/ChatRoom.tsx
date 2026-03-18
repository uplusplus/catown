import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Send, User, Bot, ArrowLeft, Sparkles, Loader } from 'lucide-react';
import axios from 'axios';
import { format } from 'date-fns';

interface Message {
  id: number;
  content: string;
  agent_name: string | null;
  message_type: string;
  created_at?: string;
}

export default function ChatRoom() {
  const { chatroomId } = useParams<{ chatroomId: string }>();
  const navigate = useNavigate();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchMessages();
  }, [chatroomId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const fetchMessages = async () => {
    try {
      const response = await axios.get(`/api/chatrooms/${chatroomId}/messages`);
      setMessages(response.data);
    } catch (error) {
      console.error('Error fetching messages:', error);
    }
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleSend = async () => {
    if (!input.trim()) return;

    const userMessage = input;
    setInput('');
    setLoading(true);

    try {
      const response = await axios.post(`/api/chatrooms/${chatroomId}/messages`, {
        content: userMessage
      });

      setMessages(prev => [...prev, response.data]);

      // Simulate agent response
      setTimeout(() => {
        setMessages(prev => [...prev, {
          id: Date.now(),
          content: 'Agent is thinking...',
          agent_name: 'assistant',
          message_type: 'text'
        }]);
      }, 1000);

    } catch (error) {
      console.error('Error sending message:', error);
      alert('Failed to send message');
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-lg shadow-sm border-b border-slate-200/50 sticky top-0 z-40">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center">
          <button
            onClick={() => navigate('/')}
            className="mr-4 p-2 text-slate-600 hover:text-primary-600 hover:bg-primary-50 rounded-lg transition-all"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="flex items-center space-x-3">
            <div className="text-3xl">💬</div>
            <div>
              <h1 className="text-xl font-semibold text-slate-900">Chat Room #{chatroomId}</h1>
              <p className="text-xs text-slate-500">Multi-Agent Collaboration</p>
            </div>
          </div>
        </div>
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-4xl mx-auto space-y-4">
          {messages.length === 0 ? (
            <div className="text-center py-16">
              <div className="inline-block p-6 bg-white rounded-2xl shadow-lg border border-slate-200">
                <div className="text-6xl mb-4">💭</div>
                <h3 className="text-xl font-semibold text-slate-900 mb-2">Start the Conversation</h3>
                <p className="text-slate-600">Send a message to begin collaborating with AI agents</p>
              </div>
            </div>
          ) : (
            messages.map((message, index) => (
              <div
                key={message.id}
                className={`flex ${message.agent_name ? 'justify-start' : 'justify-end'} animate-fade-in`}
              >
                {message.agent_name && (
                  <div className="mr-3 mt-1">
                    <div className="w-8 h-8 bg-gradient-to-br from-purple-500 to-indigo-500 rounded-full flex items-center justify-center shadow">
                      <Bot className="w-5 h-5 text-white" />
                    </div>
                  </div>
                )}
                
                <div className={`max-w-2xl ${
                  message.agent_name === null 
                    ? 'bg-gradient-to-br from-primary-600 to-indigo-600 text-white rounded-2xl rounded-br-sm shadow-lg'
                    : message.content === 'Agent is thinking...'
                    ? 'bg-slate-100 text-slate-700 rounded-2xl rounded-bl-sm shadow'
                    : 'bg-white text-slate-800 rounded-2xl rounded-bl-sm shadow border border-slate-200'
                }`}>
                  <div className="px-4 py-3">
                    {message.agent_name && message.content !== 'Agent is thinking...' && (
                      <div className="flex items-center space-x-2 mb-2">
                        <span className={`text-xs font-semibold ${
                          message.agent_name === null ? 'text-white/80' : 'text-primary-600'
                        }`}>
                          {message.agent_name}
                        </span>
                        {message.content === 'Agent is thinking...' && (
                          <Loader className="w-3 h-3 animate-spin text-slate-500" />
                        )}
                      </div>
                    )}
                    
                    {message.content === 'Agent is thinking...' ? (
                      <div className="flex items-center space-x-2 text-slate-500">
                        <Loader className="w-4 h-4 animate-spin" />
                        <span className="text-sm italic">{message.content}</span>
                      </div>
                    ) : (
                      <div className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</div>
                    )}
                    
                    {message.created_at && (
                      <div className={`text-xs mt-2 ${
                        message.agent_name === null ? 'text-white/60' : 'text-slate-400'
                      }`}>
                        {format(new Date(message.created_at), 'HH:mm')}
                      </div>
                    )}
                  </div>
                </div>
                
                {!message.agent_name && (
                  <div className="ml-3 mt-1">
                    <div className="w-8 h-8 bg-gradient-to-br from-slate-600 to-slate-700 rounded-full flex items-center justify-center shadow">
                      <User className="w-5 h-5 text-white" />
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="bg-white/80 backdrop-blur-lg border-t border-slate-200/50 p-4">
        <div className="max-w-4xl mx-auto">
          <div className="flex space-x-3 items-end">
            <div className="flex-1 relative">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Type your message..."
                className="w-full px-4 py-3 border-2 border-slate-200 rounded-xl focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all resize-none text-slate-800"
                disabled={loading}
                rows={1}
                style={{ minHeight: '48px', maxHeight: '120px' }}
              />
            </div>
            <button
              onClick={handleSend}
              disabled={!input.trim() || loading}
              className="px-6 py-3 bg-gradient-to-r from-primary-600 to-indigo-600 text-white rounded-xl hover:from-primary-700 hover:to-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-lg hover:shadow-xl font-medium flex items-center space-x-2"
            >
              {loading ? (
                <Loader className="w-5 h-5 animate-spin" />
              ) : (
                <>
                  <Send className="w-5 h-5" />
                  <span>Send</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
