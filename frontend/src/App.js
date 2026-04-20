import React, { useState } from 'react';
import axios from 'axios';
import './App.css';

function App() {
  const [files, setFiles] = useState([]);
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // upload file function
  const handleFileUpload = async (e) => {
    e.preventDefault();
    if (files.length === 0) {
      alert("Please select at least one PDF file!");
      return;
    }

    const formData = new FormData();
    files.forEach(file => {
      formData.append('pdf_files', file);
    });

    try {
      setIsLoading(true);
      // call API upload file in Node
      const response = await axios.post('http://localhost:5001/api/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });

      const newUploadedFiles = response.data.documents.map(doc => doc.filename);
      setUploadedFiles(prev => [...prev, ...newUploadedFiles]);
      setFiles([]); // clear input state
      alert(`Upload File Successful! Successfully loaded ${newUploadedFiles.length} files into AI.`);
    } catch (error) {
      console.error("upload error:", error);
      alert("Upload failed, please retry");
    } finally {
      setIsLoading(false);
    }
  };

  // handle send message function
  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!input.trim()) return;

    const userMessage = { role: 'user', content: input };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    try {
      // Call NodeJS port 5001 to ask AI
      const response = await axios.post('http://localhost:5001/api/chat', {
        question: userMessage.content
      });

      const aiMessage = {
        role: 'ai',
        content: response.data.answer,
        sources: response.data.sources
      };
      setMessages(prev => [...prev, aiMessage]);
    } catch (error) {
      console.error("chat error:", error);
      setMessages(prev => [...prev, { role: 'ai', content: 'Error! AI model disconnect' }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={{ padding: '20px', fontFamily: 'Arial, sans-serif', maxWidth: '800px', margin: '0 auto' }}>
      <h1>AI Intern - RAG System</h1>

      {/* Upload File Section */}
      <div style={{ border: '1px solid #ccc', padding: '15px', marginBottom: '20px', borderRadius: '8px' }}>
        <h3>1. Upload multiple PDF files</h3>
        <input type="file" multiple accept="application/pdf" onChange={(e) => setFiles(Array.from(e.target.files))} />
        <button onClick={handleFileUpload} disabled={isLoading || files.length === 0} style={{ padding: '8px 15px', marginLeft: '10px' }}>
          {isLoading ? "Processing..." : "Upload & Read PDFs"}
        </button>
        {uploadedFiles.length > 0 && (
          <div style={{ marginTop: '10px' }}>
            <p style={{ color: 'green', fontWeight: 'bold' }}>System is ready. AI has read {uploadedFiles.length} files:</p>
            <ul style={{ fontSize: '0.9em', color: '#555' }}>
              {uploadedFiles.map((fname, idx) => <li key={idx}>{fname}</li>)}
            </ul>
          </div>
        )}
      </div>

      {/* Chat Section */}
      <div style={{ border: '1px solid #ccc', borderRadius: '8px', padding: '15px', height: '400px', display: 'flex', flexDirection: 'column' }}>
        <h3>2. Chat with Document</h3>

        {/* Message Display Area */}
        <div style={{ flex: 1, overflowY: 'auto', marginBottom: '15px', padding: '10px', backgroundColor: '#f9f9f9', borderRadius: '5px' }}>
          {messages.length === 0 ? (
            <p style={{ color: '#888', textAlign: 'center' }}>Ask a question about the uploaded document.</p>
          ) : (
            messages.map((msg, index) => (
              <div key={index} style={{ marginBottom: '15px', textAlign: msg.role === 'user' ? 'right' : 'left' }}>
                <div style={{
                  display: 'inline-block',
                  padding: '10px',
                  borderRadius: '8px',
                  backgroundColor: msg.role === 'user' ? '#007bff' : '#e9ecef',
                  color: msg.role === 'user' ? 'white' : 'black',
                  maxWidth: '80%'
                }}>
                  <strong>{msg.role === 'user' ? 'You: ' : 'AI: '}</strong>
                  {msg.content}
                </div>

                {/* Sources */}
                {msg.sources && msg.sources.length > 0 && (
                  <div style={{ fontSize: '0.8em', color: '#555', marginTop: '5px', textAlign: 'left' }}>
                    <em>Source: page {msg.sources[0].page}. Snippet: "{msg.sources[0].snippet}"</em>
                  </div>
                )}
              </div>
            ))
          )}
          {isLoading && <p style={{ color: '#888' }}>AI Thinking...</p>}
        </div>

        {/* Message Input */}
        <form onSubmit={handleSendMessage} style={{ display: 'flex' }}>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={uploadedFiles.length === 0 ? "Please upload documents first!" : "Ask a question about the uploaded documents"}
            style={{ flex: 1, padding: '10px', borderRadius: '5px', border: '1px solid #ccc' }}
            disabled={isLoading || uploadedFiles.length === 0}
          />
          <button type="submit" disabled={isLoading || uploadedFiles.length === 0} style={{ padding: '10px 20px', marginLeft: '10px', borderRadius: '5px' }}>
            Send
          </button>
        </form>
      </div>
    </div>
  );
}

export default App;