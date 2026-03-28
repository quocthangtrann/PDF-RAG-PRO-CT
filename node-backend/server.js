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

app.post('/api/upload', upload.single('pdf_file'), (req, res) => {
    if (!req.file) return res.status(400).json({ error: "Vui lòng tải lên một file PDF" });
    const filename = req.file.filename;
    
    db.run(`INSERT INTO documents (filename) VALUES (?)`, [filename], async function(err) {
        if (err) return res.status(500).json({ error: err.message });
        
        const docId = this.lastID;
        // Lấy đường dẫn tuyệt đối của file vừa lưu trên máy Mac
        const absolutePath = path.resolve(req.file.path);

        try {
            // "GỌI ĐIỆN" BÁO CHO PYTHON NẠP FILE MỚI NÀY VÀO NÃO AI
            await axios.post('http://127.0.0.1:8000/api/ingest', {
                document_id: docId,
                file_path: absolutePath
            });

            res.json({ message: "Tải và nạp AI thành công", document_id: docId, filename: filename });
        } catch (error) {
            console.error("Lỗi khi báo Python đọc file:", error.message);
            res.status(500).json({ error: "Tải file thành công nhưng AI nạp dữ liệu thất bại." });
        }
    });
});

// api connect to ai model
app.post('/api/chat', async (req, res) => {
    const { document_id, question } = req.body;
    if (!question) return res.status(400).json({ error: "Câu hỏi không được để trống" });
    if (!document_id) return res.status(400).json({ error: "Vui lòng tải file PDF lên trước" });

    try {
        // Gọi Python và truyền theo document_id để Python biết đang hỏi file nào
        const pythonResponse = await axios.post('http://127.0.0.1:8000/api/ask', {
            document_id: document_id,
            question: question
        });

        const ai_answer = pythonResponse.data.answer;

        db.run(`INSERT INTO chats (document_id, user_query, ai_response) VALUES (?, ?, ?)`, 
            [document_id, question, ai_answer], 
            function(err) {
                if (err) console.error("Lỗi lưu lịch sử chat:", err);
            }
        );

        res.json({
            status: "success",
            question: question,
            answer: ai_answer,
            sources: pythonResponse.data.sources
        });

    } catch (error) {
        console.error("❌ Lỗi khi gọi Python AI:", error?.response?.data || error.message);
        res.status(500).json({ error: "Hệ thống AI đang bận hoặc không tìm thấy tài liệu." });
    }
});

// Giữ cho server không bao giờ chết
process.on('uncaughtException', function (err) {
    console.error("Bắt được lỗi ngầm:", err);
});

// Khởi động Server
app.listen(PORT, () => {
    console.log(`🚀 NodeJS Backend đang chạy CỰC KỲ ỔN ĐỊNH tại http://localhost:${PORT}`);
    console.log(`(Hãy để cửa sổ Terminal này chạy nguyên như vậy)`);
});