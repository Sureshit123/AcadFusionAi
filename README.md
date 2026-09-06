# AcadFusion AI

AcadFusion AI is a comprehensive academic management suite designed to streamline departmental operations. It combines advanced data scraping, intelligent scheduling algorithms, and a premium user interface to provide a seamless experience for academic administrators.

## 🚀 Key Modules

### 1. VTU Result Analyzer
- **Automated Scraping**: Fetch results for multiple USNs via sequential auto-generation or bulk file upload.
- **Session Persistence**: Optimized engine that reuses connections to minimize captcha prompts and improve stability. 
- **Intelligent Processing**: Automatically extracts student data, SGPA, grades, and subject details.
- **Official ISE Department Reporting**: Generates a professional multi-sheet Excel report including:
  - **Overall Result Summary**: High-level class performance with signature footers.
  - **Subject-wise Analysis**: Detailed per-subject grading with a "Subject wise %" graphical dashboard.
  - **All Students List**: Advanced multi-level headers with automatic **backlog highlighting** (Bright Yellow) for instant identification.
- **Mock Mode**: Built-in simulator for testing without hitting live VTU servers.

### 2. Departmental Timetable Generator
- **High-Performance Engine**: Optimized with a pre-fetching cache to generate complex schedules ~90% faster.
- **Smart Scheduling**: Generates conflict-free timetables for multiple semesters simultaneously.
- **Teacher Resting Slots (New)**: Enforces a mandatory 1-hour "Resting Slot" (Empty or Fixed) between subject classes for faculty to prevent continuous teaching fatigue.
- **Historical Modification**: Ability to load, modify, and regenerate previously saved timetables with one click.
- **Resource Management**: Strictly enforces a "one class per subject per day" rule and prevents teacher/lab double-booking.
- **Export to Excel**: One-click generation of professional timetable spreadsheets with individual semester sheets.

### 3. Secure Admin Panel
- **Authentication**: Secure login system for authorized department personnel.
- **Dynamic Configuration**: Easily configure teacher pools, lab rooms, and semester subjects.

## 🎨 Design Aesthetics
- **Premium UI**: Modern dark-themed interface with glassmorphism effects.
- **Responsive Layout**: Works seamlessly across desktops, tablets, and mobile devices.
- **Real-time Feedback**: Dynamic progress bars and live status updates during processing tasks.

---

## 🛠️ Technology Stack
- **Backend**: Flask (Python)
- **Frontend**: HTML5, CSS3 (Vanilla), JavaScript (ES6+)
- **Data Processing**: Pandas, OpenPyXL, BeautifulSoup4
- **Database**: MongoDB (with Atlas support)
- **Environment**: Decoupled configuration via `.env`

---

## ⚙️ Local Setup Instructions

1. **Clone the Repository**
   ```bash
   git clone https://github.com/Sureshit123/AcadFusion-Ai.git
   cd AcadFusion-Ai
   ```

2. **Setup a Virtual Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration**
   Create a `.env` file in the root directory (see `.env.example`):
   ```env
   MONGODB_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/acadfusion_ai?retryWrites=true&w=majority
   FLASK_SECRET_KEY=your_secret_key
   ADMIN_EMAIL=sharmasreshit@gmail.com
   ADMIN_PASSWORD=change-me-to-a-strong-password
   ```

5. **Run the Application**
   ```bash
   python app.py
   ```
   Access the app at `http://localhost:5000`.

---

## 🚀 Deployment

Designed to be easily deployed on **Render**, **Railway**, or **Vercel**.

### Render.com Guide
1. Connect project GitHub repository.
2. Select **Web Service**.
3. **Environment**: Python 3.
4. **Build Command**: `pip install -r requirements.txt`
5. **Start Command**: `gunicorn app:app` (The project includes a `Procfile` for auto-detection).
6. **Config**: Add your `.env` variables (`MONGODB_URI`, `ADMIN_EMAIL`, `FLASK_SECRET_KEY`, etc.) in the Dashboard.

---

## 📄 License
This project is licensed under the MIT License - see the LICENSE file for details.

Developed with ❤️ for Academic Excellence.

---

Last updated: 2026-07-03
