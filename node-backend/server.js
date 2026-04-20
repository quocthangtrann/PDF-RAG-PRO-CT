const express = require('express');
const cors = require('cors');
const axios = require('axios');
const sqlite3 = require('sqlite3').verbose();
const multer = require('multer');
const path = require('path');
const fs = require('fs');

const app = express();

const PORT = 5001; 

app.use(cors());
app.use(express.json());

// create uploads folder 
const uploadDir = './uploads';
if (!fs.existsSync(uploadDir)){
    fs.mkdirSync(uploadDir);
}

// database setup
const db = new sqlite3.Database('./database.sqlite', (err) => {
    if (err) console.error("connect error Database:", err);
    else console.log("connect successfull SQLite Database");
});

db.serialize(() => {
    db.run(`CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        upload_date DATETIME DEFAULT CURRENT_TIMESTAMP
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER,
        user_query TEXT NOT NULL,
        ai_response TEXT NOT NULL,
        chat_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(document_id) REFERENCES documents(id)
    )`);
});

// upload file api setup
const storage = multer.diskStorage({
    destination: './uploads/',
    filename: function (req, file, cb) {
        cb(null, 'DOC-' + Date.now() + path.extname(file.originalname));
    }
});
const upload = multer({ storage: storage });

app.post('/api/upload', upload.array('pdf_files', 50), async (req, res) => {
    if (!req.files || req.files.length === 0) return res.status(400).json({ error: "Please upload at least one PDF file" });
    
    try {
        const uploadedDocs = [];
        
        for (const file of req.files) {
            const filename = file.filename;
            
            const docId = await new Promise((resolve, reject) => {
                db.run(`INSERT INTO documents (filename) VALUES (?)`, [filename], function(err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                });
            });

            // Get absolute path of the saved file
            const absolutePath = path.resolve(file.path);

            // Call Python to ingest the new file into AI brain
            await axios.post('http://127.0.0.1:8000/api/ingest', {
                document_id: docId,
                file_path: absolutePath
            });

            uploadedDocs.push({ document_id: docId, filename: filename });
        }

        res.json({ message: "Successfully uploaded and ingested all files", documents: uploadedDocs });
    } catch (error) {
        console.error("Error calling Python to read file:", error.message);
        res.status(500).json({ error: "Failed to ingest data into AI systems.", details: error.message });
    }
});

// api connect to ai model
app.post('/api/chat', async (req, res) => {
    const { question } = req.body;
    if (!question) return res.status(400).json({ error: "Question cannot be empty" });
    // No need to catch missing document_id as we query global knowledge base

    try {
        // Call Python to ask
        const pythonResponse = await axios.post('http://127.0.0.1:8000/api/ask', {
            question: question
        });

        const ai_answer = pythonResponse.data.answer;

        // document_id can be null due to global query
        db.run(`INSERT INTO chats (document_id, user_query, ai_response) VALUES (?, ?, ?)`, 
            [null, question, ai_answer], 
            function(err) {
                if (err) console.error("Error saving chat history:", err);
            }
        );

        res.json({
            status: "success",
            question: question,
            answer: ai_answer,
            sources: pythonResponse.data.sources
        });

    } catch (error) {
        console.error("Error calling Python AI:", error?.response?.data || error.message);
        res.status(500).json({ error: "AI system is busy or documents not found. (Please upload documents first)" });
    }
});

// Keep server alive
process.on('uncaughtException', function (err) {
    console.error("Caught unhandled exception:", err);
});

// Start Server
app.listen(PORT, () => {
    console.log(`NodeJS Backend is running perfectly at http://localhost:${PORT}`);
    console.log(`(Leave this terminal window running)`);
});