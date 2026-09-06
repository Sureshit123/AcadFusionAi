function updateResultTypeUI() {
    const selectedRadio = document.querySelector('input[name="result_type"]:checked');
    const selectedVal = selectedRadio ? selectedRadio.value : 'regular';
    const linkLabel = document.getElementById('vtu_link_label');
    
    if (linkLabel) {
        if (selectedVal === 'reeval') {
            linkLabel.innerText = 'Re-evaluation VTU Result Link';
        } else if (selectedVal === 'makeup') {
            linkLabel.innerText = 'Make-up VTU Result Link';
        } else {
            linkLabel.innerText = 'Regular VTU Result Link';
        }
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', updateResultTypeUI);
} else {
    updateResultTypeUI();
}

function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    
    if (tabName === 'range') {
        document.querySelectorAll('.tab')[0].classList.add('active');
        document.getElementById('range-tab').classList.add('active');
    } else {
        document.querySelectorAll('.tab')[1].classList.add('active');
        document.getElementById('bulk-tab').classList.add('active');
    }
}

let progressInterval;

async function startJob(formData, submitBtnId) {
    const submitBtn = document.getElementById(submitBtnId);
    submitBtn.disabled = true;
    submitBtn.innerHTML = 'Starting...';
    
    document.getElementById('progressArea').style.display = 'block';
    document.getElementById('downloadBtn').style.display = 'none';
    if (document.getElementById('downloadPdfBtn')) document.getElementById('downloadPdfBtn').style.display = 'none';
    if (document.getElementById('downloadCsvBtn')) document.getElementById('downloadCsvBtn').style.display = 'none';
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('currentStatus').innerText = 'Initializing scraper...';
    document.getElementById('currentStatus').style.color = 'var(--text-secondary)';

    // Section 5: Append report configuration settings to form data
    const reportDept = document.getElementById('report_dept');
    const reportAy = document.getElementById('report_ay');
    const reportExam = document.getElementById('report_exam');
    const reportSem = document.getElementById('report_sem');
    const reportScheme = document.getElementById('report_scheme');
    const resultTypeRadio = document.querySelector('input[name="result_type"]:checked');
    const vtuLinkInput = document.getElementById('vtu_result_link');

    if (reportDept) formData.append('department', reportDept.value);
    if (reportAy) formData.append('academic_year', reportAy.value);
    if (reportExam) formData.append('examination', reportExam.value);
    if (reportSem) formData.append('semester', reportSem.value);
    if (reportScheme) formData.append('scheme', reportScheme.value);
    if (resultTypeRadio) formData.append('result_type', resultTypeRadio.value);
    if (vtuLinkInput && vtuLinkInput.value.trim()) formData.append('vtu_result_link', vtuLinkInput.value.trim());


    try {
        const response = await fetch('/api/start_analysis', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.error || 'Failed to start job');
        }
        
        const data = await response.json();
        pollProgress(data.job_id, submitBtn, submitBtnId);
    } catch (error) {
        alert("Error: " + error.message);
        submitBtn.disabled = false;
        submitBtn.innerHTML = submitBtnId.includes('Range') ? 'Start Analysis' : 'Process File';
        document.getElementById('progressArea').style.display = 'none';
    }
}

const rangeForm = document.getElementById('rangeForm');
if (rangeForm) {
    rangeForm.addEventListener('submit', function(e) {
        e.preventDefault();
        startJob(new FormData(this), 'startBtnRange');
    });
}

const bulkForm = document.getElementById('bulkForm');
if (bulkForm) {
    bulkForm.addEventListener('submit', function(e) {
        e.preventDefault();
        startJob(new FormData(this), 'startBtnBulk');
    });
}

let isWaitingForCaptcha = false;
let currentJobId = null;
let lastAttemptFailed = false;
let currentQueueUsn = null; // USN that the currently-open CAPTCHA modal belongs to

function pollProgress(jobId, btnRef, btnOriginalId) {
    currentJobId = jobId;
    const texts = {
        'startBtnRange': 'Start Analysis',
        'startBtnBulk': 'Process File'
    };

    progressInterval = setInterval(async () => {
        if (isWaitingForCaptcha) return; // Pause polling while modal is open

        try {
            const res = await fetch(`/api/progress/${jobId}`);
            if (!res.ok) throw new Error('Failed to fetch progress');
            
            const data = await res.json();
            
            // Always track the authoritative queue USN from the backend
            currentQueueUsn = data.queue_usn || data.current_usn || null;

            const percent = data.total > 0 ? (data.completed / data.total) * 100 : 0;
            document.getElementById('progressBar').style.width = `${percent}%`;
            document.getElementById('progressText').innerText = `${data.completed} / ${data.total}`;
            
            // Use startsWith to match 'Waiting for Captcha' regardless of appended text
            if (data.status && data.status.startsWith('Waiting for Captcha')) {
                document.getElementById('currentStatus').innerText = "Action Required: Solve CAPTCHA for " + (currentQueueUsn || '');
                document.getElementById('currentStatus').style.color = 'var(--text-primary)';
                
                // If modal already open for a DIFFERENT USN, that's a desync — close and reopen
                if (isWaitingForCaptcha && currentQueueUsn && document.getElementById('captchaUsn').innerText !== currentQueueUsn) {
                    console.warn('[CAPTCHA DESYNC] Modal was open for', document.getElementById('captchaUsn').innerText, 'but queue is now on', currentQueueUsn, '— refreshing modal.');
                    document.getElementById('captchaModal').style.display = 'none';
                    isWaitingForCaptcha = false;
                }

                if (!isWaitingForCaptcha) {
                    // Show Modal
                    isWaitingForCaptcha = true;
                    document.getElementById('captchaUsn').innerText = currentQueueUsn || data.current_usn;
                    document.getElementById('captchaImage').src = "data:image/png;base64," + data.captcha_base64;
                    document.getElementById('captchaInput').value = '';
                    document.getElementById('submitCaptchaBtn').disabled = false;
                    
                    // Show Error if retry
                    const errorEl = document.getElementById('captchaError');
                    if (lastAttemptFailed) {
                        errorEl.style.display = 'block';
                        errorEl.innerText = 'Invalid CAPTCHA code. Please try again.';
                        // Shake effect
                        const modal = document.querySelector('.modal-content');
                        modal.style.animation = 'none';
                        setTimeout(() => modal.style.animation = 'shake 0.4s ease-in-out', 10);
                    } else {
                        errorEl.style.display = 'none';
                    }
                    
                    document.getElementById('captchaModal').style.display = 'flex';
                    document.getElementById('captchaInput').focus();
                }
                return;
            }

            if (data.status && data.status.includes('Invalid Captcha')) {
                lastAttemptFailed = true;
            } else if (data.status && data.status.includes('Scraping')) {
                lastAttemptFailed = false; // Reset on progress
            }
            
            if (data.status === 'Completed') {
                clearInterval(progressInterval);
                btnRef.disabled = false;
                btnRef.innerHTML = texts[btnOriginalId];
                
                const downloadBtn = document.getElementById('downloadBtn');
                const downloadPdfBtn = document.getElementById('downloadPdfBtn');
                const downloadCsvBtn = document.getElementById('downloadCsvBtn');
                const viewBtn = document.getElementById('viewResultsBtn');
                
                if (downloadBtn) {
                    downloadBtn.href = `/download/${jobId}`;
                    downloadBtn.style.display = 'flex';
                }
                if (downloadPdfBtn) {
                    downloadPdfBtn.href = `/download_pdf/${jobId}`;
                    downloadPdfBtn.style.display = 'flex';
                }
                if (downloadCsvBtn) {
                    downloadCsvBtn.href = `/download_csv/${jobId}`;
                    downloadCsvBtn.style.display = 'flex';
                }
                if (viewBtn) {
                    viewBtn.style.display = 'flex';
                    viewBtn.setAttribute('onclick', `openResultsPreview('${jobId}')`);
                }
                
                document.getElementById('currentStatus').innerHTML = '<span style="color: #10b981">Analysis Complete!</span>';
                
            } else if (data.status && data.status.includes('Error')) {
                clearInterval(progressInterval);
                document.getElementById('currentStatus').innerText = data.status;
                document.getElementById('currentStatus').style.color = 'var(--error)';
                btnRef.disabled = false;
                btnRef.innerHTML = texts[btnOriginalId];
            } else {
                document.getElementById('currentStatus').innerText = 
                    data.status === 'Running' ? `Scraping: ${data.current_usn}` : data.status;
            }
            
        } catch (error) {
            console.error(error);
            clearInterval(progressInterval);
            document.getElementById('currentStatus').innerText = "Connection lost during polling.";
            document.getElementById('currentStatus').style.color = 'var(--error)';
            btnRef.disabled = false;
            btnRef.innerHTML = texts[btnOriginalId];
        }
    }, 1000);
}

// Modal tab controls
function switchModalTab(tabName) {
    document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    document.getElementById('modal-list-container').style.display = 'none';
    document.getElementById('modal-charts-container').style.display = 'none';
    
    if (tabName === 'list') {
        document.getElementById('modal-tab-list').classList.add('active');
        document.getElementById('modal-list-container').style.display = 'flex';
    } else {
        document.getElementById('modal-tab-charts').classList.add('active');
        document.getElementById('modal-charts-container').style.display = 'block';
    }
}

// Dynamic Charts management
let chartInstances = {};
function destroyExistingCharts() {
    Object.values(chartInstances).forEach(c => c.destroy());
    chartInstances = {};
}

async function openResultsPreview(job_id) {
    const modal = document.getElementById('resultsModal');
    const body = document.getElementById('previewBody');
    body.innerHTML = '<tr><td colspan="6" style="text-align:center">Loading dashboard data...</td></tr>';
    
    // Switch to first tab as default
    switchModalTab('list');
    modal.style.display = 'flex';
    
    try {
        const res = await fetch(`/api/job_results/${job_id}`);
        const data = await res.json();
        
        let results = [];
        let mappings = {};
        let settings = {};
        
        if (Array.isArray(data)) {
            results = data;
        } else {
            results = data.results || [];
            mappings = data.subject_mappings || {};
            settings = data.report_settings || {};
        }
        
        if (!results || results.length === 0) {
            body.innerHTML = '<tr><td colspan="6" style="text-align:center">No student records found.</td></tr>';
            return;
        }
        
        // Update Title if settings are available
        const titleText = document.getElementById('modal-title-text');
        if (titleText && settings.department) {
            titleText.innerText = `${settings.department} - Sem ${settings.semester} Result Analysis (${settings.academic_year})`;
        }
        
        // Calculate Metrics
        let total = results.length;
        let appeared = 0;
        let passed = 0;
        let failed = 0;
        let sumSgpa = 0;
        let sgpas = [];
        
        let classDist = { distinction: 0, first: 0, second: 0, pass: 0, fail: 0 };
        let backlogs = { b0: 0, b1: 0, b2: 0, b3plus: 0 };
        let gradeCounts = { O: 0, 'A+': 0, A: 0, 'B+': 0, B: 0, C: 0, P: 0, F: 0 };
        let subjectStats = {}; // sub_code -> { appeared, passed, totalMarks }
        let creditPerformance = {}; // credits -> { totalScore, count }
        let facultyStats = {}; // facultyName -> { appeared, passed }

        results.forEach(r => {
            const status = r.status || 'Fail';
            const sgpa = parseFloat(r.sgpa) || 0.0;
            
            if (status === 'Pass' || status === 'Fail') {
                appeared++;
                if (status === 'Pass') {
                    passed++;
                    if (sgpa >= 7.75) classDist.distinction++;
                    else if (sgpa >= 6.75) classDist.first++;
                    else if (sgpa >= 5.75) classDist.second++;
                    else classDist.pass++;
                } else {
                    failed++;
                    classDist.fail++;
                }
                
                if (sgpa > 0.1) {
                    sumSgpa += sgpa;
                    sgpas.push(sgpa);
                }
                
                // Count Backlogs & Subject specific marks
                let studentBacklogs = 0;
                Object.entries(r.subjects || {}).items = Object.entries(r.subjects || {}).forEach(([code, s]) => {
                    const res = (s.result || '').toUpperCase();
                    const totalMarks = parseInt(s.total) || 0;
                    
                    // Grades mapping
                    if (res === 'F' || res === 'A' || res === 'ABSENT' || res === 'FAIL') {
                        studentBacklogs++;
                        gradeCounts.F++;
                    } else {
                        if (totalMarks >= 90) gradeCounts.O++;
                        else if (totalMarks >= 80) gradeCounts['A+']++;
                        else if (totalMarks >= 70) gradeCounts.A++;
                        else if (totalMarks >= 60) gradeCounts['B+']++;
                        else if (totalMarks >= 50) gradeCounts.B++;
                        else if (totalMarks >= 45) gradeCounts.C++;
                        else gradeCounts.P++;
                    }
                    
                    // Subject Metrics
                    if (!subjectStats[code]) {
                        subjectStats[code] = { name: s.name, code: code, appeared: 0, passed: 0, sumMarks: 0 };
                    }
                    subjectStats[code].appeared++;
                    if (res === 'P' || res === 'PASS') {
                        subjectStats[code].passed++;
                    }
                    subjectStats[code].sumMarks += totalMarks;
                    
                    // Credits performance mapping
                    const mappedInfo = mappings[code] || {};
                    const credits = mappedInfo.credits || 4;
                    if (!creditPerformance[credits]) {
                        creditPerformance[credits] = { sumMarks: 0, count: 0 };
                    }
                    creditPerformance[credits].sumMarks += totalMarks;
                    creditPerformance[credits].count++;
                    
                    // Faculty performance mapping
                    const facultyName = mappedInfo.faculty_name || 'Unassigned';
                    if (!facultyStats[facultyName]) {
                        facultyStats[facultyName] = { appeared: 0, passed: 0 };
                    }
                    facultyStats[facultyName].appeared++;
                    if (res === 'P' || res === 'PASS') {
                        facultyStats[facultyName].passed++;
                    }
                });
                
                if (studentBacklogs === 0) backlogs.b0++;
                else if (studentBacklogs === 1) backlogs.b1++;
                else if (studentBacklogs === 2) backlogs.b2++;
                else backlogs.b3plus++;
            }
        });
        
        // Render Summary Stats Cards
        const passPercent = appeared > 0 ? ((passed / appeared) * 100).toFixed(1) : '0';
        const avgSgpa = sgpas.length > 0 ? (sumSgpa / sgpas.length).toFixed(2) : '0.0';
        const highestSgpa = sgpas.length > 0 ? Math.max(...sgpas).toFixed(2) : '0.0';
        
        document.getElementById('stat-total').innerText = total;
        document.getElementById('stat-appeared').innerText = appeared;
        document.getElementById('stat-passed').innerText = passed;
        document.getElementById('stat-failed').innerText = failed;
        document.getElementById('stat-pass-per').innerText = `${passPercent}%`;
        document.getElementById('stat-avg-sgpa').innerText = avgSgpa;
        document.getElementById('stat-high-sgpa').innerText = highestSgpa;
        
        document.getElementById('stat-dist').innerText = classDist.distinction;
        document.getElementById('stat-first').innerText = classDist.first;
        document.getElementById('stat-second').innerText = classDist.second;
        document.getElementById('stat-fail-class').innerText = classDist.fail;

        // Render Roster table
        body.innerHTML = results.map(r => {
            const status = r.status || 'Fail';
            const pct = r.percentage || 0;
            const sgpa = parseFloat(r.sgpa) || 0.0;
            return `
                <tr>
                    <td style="font-weight:700; color:var(--accent-primary)">${r.usn}</td>
                    <td>${r.name || 'N/A'}</td>
                    <td style="text-align:center">${r.total_marks || 0}</td>
                    <td style="text-align:center">${pct}%</td>
                    <td style="text-align:center; font-weight:600; color:var(--accent-secondary)">${sgpa.toFixed(2)}</td>
                    <td><span class="badge ${status.toLowerCase() === 'pass' ? 'pass' : 'fail'}">${status}</span></td>
                </tr>
            `;
        }).join('');

        // Render the 11 charts
        destroyExistingCharts();
        
        // Colors palette (purple, cyan, emerald, red, gold)
        const colors = {
            purple: '#7c3aed',
            cyan: '#06b6d4',
            emerald: '#10b981',
            red: '#ef4444',
            amber: '#f59e0b',
            gray: '#475569',
            purpleGlow: 'rgba(124, 58, 237, 0.4)',
            cyanGlow: 'rgba(6, 182, 212, 0.4)',
            emeraldGlow: 'rgba(16, 185, 129, 0.4)',
            redGlow: 'rgba(239, 68, 68, 0.4)'
        };

        // 1. Pass vs Fail Pie Chart
        chartInstances['passfail'] = new Chart(document.getElementById('chart-passfail').getContext('2d'), {
            type: 'pie',
            data: {
                labels: ['Pass', 'Fail'],
                datasets: [{
                    data: [passed, failed],
                    backgroundColor: [colors.emerald, colors.red],
                    borderWidth: 1,
                    borderColor: 'rgba(255,255,255,0.08)'
                }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8' } } } }
        });

        // 2. Grade Distribution Bar Chart
        chartInstances['grades'] = new Chart(document.getElementById('chart-grades').getContext('2d'), {
            type: 'bar',
            data: {
                labels: ['O', 'A+', 'A', 'B+', 'B', 'C', 'P', 'F'],
                datasets: [{
                    label: 'Grade Count',
                    data: [gradeCounts.O, gradeCounts['A+'], gradeCounts.A, gradeCounts['B+'], gradeCounts.B, gradeCounts.C, gradeCounts.P, gradeCounts.F],
                    backgroundColor: colors.purple,
                    borderColor: colors.purpleGlow,
                    borderWidth: 1,
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#94a3b8' }, grid: { display: false } },
                    y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } }
                }
            }
        });

        // 3. SGPA Distribution Histogram
        const bins = ['<5.0', '5.0-5.9', '6.0-6.9', '7.0-7.9', '8.0-8.9', '9.0-10'];
        const binCounts = [0, 0, 0, 0, 0, 0];
        sgpas.forEach(s => {
            if (s < 5.0) binCounts[0]++;
            else if (s < 6.0) binCounts[1]++;
            else if (s < 7.0) binCounts[2]++;
            else if (s < 8.0) binCounts[3]++;
            else if (s < 9.0) binCounts[4]++;
            else binCounts[5]++;
        });
        
        chartInstances['sgpadist'] = new Chart(document.getElementById('chart-sgpa-dist').getContext('2d'), {
            type: 'bar',
            data: {
                labels: bins,
                datasets: [{
                    label: 'Students Count',
                    data: binCounts,
                    backgroundColor: colors.cyan,
                    borderColor: colors.cyanGlow,
                    borderWidth: 1,
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#94a3b8' }, grid: { display: false } },
                    y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } }
                }
            }
        });

        // 4. Subject-wise Pass Percentage Horizontal Bar
        const subCodes = Object.keys(subjectStats);
        const subPassPercentages = subCodes.map(c => ((subjectStats[c].passed / subjectStats[c].appeared) * 100).toFixed(1));
        
        chartInstances['subpass'] = new Chart(document.getElementById('chart-subpass').getContext('2d'), {
            type: 'bar',
            data: {
                labels: subCodes,
                datasets: [{
                    data: subPassPercentages,
                    backgroundColor: colors.emerald,
                    borderColor: colors.emeraldGlow,
                    borderWidth: 1,
                    borderRadius: 6
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { max: 100, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                    y: { ticks: { color: '#94a3b8' }, grid: { display: false } }
                }
            }
        });

        // 5. Top 10 Students Vertical Bar Chart
        const topStudents = [...results]
            .filter(r => r.status === 'Pass')
            .sort((a, b) => (parseFloat(b.sgpa) || 0) - (parseFloat(a.sgpa) || 0))
            .slice(0, 10);
            
        chartInstances['topstudents'] = new Chart(document.getElementById('chart-topstudents').getContext('2d'), {
            type: 'bar',
            data: {
                labels: topStudents.map(s => s.name ? (s.name.length > 12 ? s.name.substring(0, 10) + '..' : s.name) : s.usn),
                datasets: [{
                    label: 'SGPA',
                    data: topStudents.map(s => parseFloat(s.sgpa) || 0.0),
                    backgroundColor: colors.purple,
                    borderColor: colors.purpleGlow,
                    borderWidth: 1,
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#94a3b8' }, grid: { display: false } },
                    y: { max: 10, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } }
                }
            }
        });

        // 6. Average Subject Score Column Chart
        const subAvgScores = subCodes.map(c => (subjectStats[c].sumMarks / subjectStats[c].appeared).toFixed(1));
        chartInstances['avgscore'] = new Chart(document.getElementById('chart-avgscore').getContext('2d'), {
            type: 'bar',
            data: {
                labels: subCodes,
                datasets: [{
                    label: 'Avg Marks',
                    data: subAvgScores,
                    backgroundColor: colors.cyan,
                    borderColor: colors.cyanGlow,
                    borderWidth: 1,
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#94a3b8' }, grid: { display: false } },
                    y: { max: 100, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } }
                }
            }
        });

        // 7. Faculty-wise Subject Result Bar
        const facultyNames = Object.keys(facultyStats);
        const facPassPercentages = facultyNames.map(f => ((facultyStats[f].passed / facultyStats[f].appeared) * 100).toFixed(1));
        chartInstances['facresult'] = new Chart(document.getElementById('chart-facresult').getContext('2d'), {
            type: 'bar',
            data: {
                labels: facultyNames.map(name => name.length > 12 ? name.substring(0,10) + '..' : name),
                datasets: [{
                    label: 'Pass %',
                    data: facPassPercentages,
                    backgroundColor: colors.purple,
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#94a3b8' } },
                    y: { max: 100, ticks: { color: '#94a3b8' } }
                }
            }
        });

        // 8. Credit-wise Performance Stacked/Grouped Bar
        const creditTypes = Object.keys(creditPerformance);
        const creditAvgScores = creditTypes.map(cr => (creditPerformance[cr].sumMarks / creditPerformance[cr].count).toFixed(1));
        chartInstances['creditperf'] = new Chart(document.getElementById('chart-creditperf').getContext('2d'), {
            type: 'bar',
            data: {
                labels: creditTypes.map(c => c + ' Credits'),
                datasets: [{
                    label: 'Average Score',
                    data: creditAvgScores,
                    backgroundColor: colors.emerald,
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#94a3b8' } },
                    y: { max: 100, ticks: { color: '#94a3b8' } }
                }
            }
        });

        // 9. Subject Difficulty Index Heatmap (Rendered as beautiful lists)
        const difficultyContainer = document.getElementById('difficulty-heatmap-container');
        if (difficultyContainer) {
            difficultyContainer.innerHTML = subCodes.map(code => {
                const stat = subjectStats[code];
                const passRate = (stat.passed / stat.appeared) * 100;
                const difficulty = 100 - passRate;
                
                let colorClass = '#10b981'; // Easy
                let label = 'Easy';
                if (difficulty > 40) { colorClass = '#ef4444'; label = 'Hard'; }
                else if (difficulty > 20) { colorClass = '#f59e0b'; label = 'Medium'; }
                
                return `
                    <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.2); padding:0.6rem 1rem; border-radius:8px; border-left: 4px solid ${colorClass}">
                        <div style="text-align:left;">
                            <strong style="color:var(--text-primary); font-size:0.85rem;">${code}</strong>
                            <div style="font-size:0.7rem; color:var(--text-secondary);">${stat.name || 'Subject'}</div>
                        </div>
                        <div style="display:flex; align-items:center; gap:1rem;">
                            <span style="font-size:0.75rem; color:var(--text-secondary)">Fail Rate: <strong>${difficulty.toFixed(0)}%</strong></span>
                            <span style="background:${colorClass}15; color:${colorClass}; border:1px solid ${colorClass}30; font-size:0.7rem; padding:0.2rem 0.5rem; border-radius:4px; font-weight:700;">${label}</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        // 10. Backlog Distribution Pie
        chartInstances['backlogs'] = new Chart(document.getElementById('chart-backlogs').getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: ['0 Backlogs', '1 Backlog', '2 Backlogs', '3+ Backlogs'],
                datasets: [{
                    data: [backlogs.b0, backlogs.b1, backlogs.b2, backlogs.b3plus],
                    backgroundColor: [colors.emerald, colors.cyan, colors.amber, colors.red],
                    borderWidth: 1,
                    borderColor: 'rgba(255,255,255,0.08)'
                }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8' } } } }
        });

        // 11. Semester Performance Radar Chart
        chartInstances['radar'] = new Chart(document.getElementById('chart-radar').getContext('2d'), {
            type: 'radar',
            data: {
                labels: subCodes,
                datasets: [{
                    label: 'Subject Average Marks',
                    data: subAvgScores,
                    fill: true,
                    backgroundColor: 'rgba(6, 182, 212, 0.2)',
                    borderColor: colors.cyan,
                    pointBackgroundColor: colors.cyan,
                    pointBorderColor: '#fff',
                    pointHoverBackgroundColor: '#fff',
                    pointHoverBorderColor: colors.cyan
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    r: {
                        angleLines: { color: 'rgba(255,255,255,0.08)' },
                        grid: { color: 'rgba(255,255,255,0.08)' },
                        pointLabels: { color: '#94a3b8', font: { size: 9 } },
                        ticks: { color: '#94a3b8', backdropColor: 'transparent', max: 100 }
                    }
                }
            }
        });

    } catch (e) {
        body.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#ef4444">Error loading results preview: ${e.message}</td></tr>`;
        console.error(e);
    }
}

async function submitCaptcha() {
    const val = document.getElementById('captchaInput').value.trim();
    if (!val) return;
    
    // Capture the queue USN this modal belongs to at the moment of submission
    const submittingForUsn = currentQueueUsn;

    document.getElementById('submitCaptchaBtn').disabled = true;
    try {
        const res = await fetch(`/api/submit_captcha/${currentJobId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                captcha: val,
                queue_usn: submittingForUsn  // Send USN for backend sync validation
            })
        });
        
        if (res.status === 409) {
            // Queue desync: backend rejected because the job has already moved on
            const errData = await res.json();
            console.warn('[CAPTCHA DESYNC] Server rejected submission:', errData);
            document.getElementById('captchaModal').style.display = 'none';
            isWaitingForCaptcha = false;
            lastAttemptFailed = false;
            document.getElementById('submitCaptchaBtn').disabled = false;
            return;
        }

        if (res.ok) {
            document.getElementById('captchaModal').style.display = 'none';
            isWaitingForCaptcha = false;
        } else {
            alert('Failed to submit captcha. Please try again.');
            document.getElementById('submitCaptchaBtn').disabled = false;
        }
    } catch(e) {
        alert('Network Error submitting captcha');
        document.getElementById('submitCaptchaBtn').disabled = false;
    }
}

// Allow Enter key
const captchaInput = document.getElementById('captchaInput');
if (captchaInput) {
    captchaInput.addEventListener('keypress', function(e) {
        if(e.key === 'Enter') submitCaptcha();
    });
}

async function toggleMockMode(cb) {
    const isLive = cb.checked;
    const labelDemo = document.getElementById('label-demo');
    const labelLive = document.getElementById('label-live');
    
    // Immediate UI feedback
    if (isLive) {
        labelLive.classList.add('active');
        labelDemo.classList.remove('active');
    } else {
        labelDemo.classList.add('active');
        labelLive.classList.remove('active');
    }
    
    try {
        const res = await fetch('/api/toggle_mock', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ use_mock: !isLive })
        });
        
        if (!res.ok) throw new Error("Failed to switch mode");
        
        console.log(`Mode switched to: ${isLive ? 'Live VTU' : 'Simulation'}`);
        
    } catch (e) {
        alert("Error switching mode: " + e.message);
        cb.checked = !isLive; // Revert
        labelLive.classList.toggle('active');
        labelDemo.classList.toggle('active');
    }
}

// Section 5: Load Departments and Settings Defaults into Scraper panel
async function loadScraperDefaults() {
    try {
        // Load Departments dropdown
        const deptRes = await fetch('/api/settings/departments');
        const depts = await deptRes.json();
        const deptSel = document.getElementById('report_dept');
        if (deptSel) {
            deptSel.innerHTML = '<option value="">Select Department</option>' + 
                depts.map(d => `<option value="${d.name}">${d.name} (${d.code})</option>`).join('');
        }

        // Load Default configs
        const defRes = await fetch('/api/settings/defaults');
        const data = await defRes.json();
        if (data.department) {
            if (deptSel) deptSel.value = data.department;
            if (document.getElementById('report_ay')) document.getElementById('report_ay').value = data.academic_year;
            if (document.getElementById('report_exam')) document.getElementById('report_exam').value = data.examination;
            if (document.getElementById('report_sem')) document.getElementById('report_sem').value = data.semester;
            if (document.getElementById('report_scheme')) document.getElementById('report_scheme').value = data.scheme;
        }
    } catch(e) { console.error('Defaults load error:', e); }
}

// Initial state cleanup
window.addEventListener('DOMContentLoaded', () => {
    const cb = document.getElementById('modeToggle');
    if (cb) {
        const labelDemo = document.getElementById('label-demo');
        const labelLive = document.getElementById('label-live');
        // If checked, it means LIVE
        if (cb.checked) {
            labelLive.classList.add('active');
            labelDemo.classList.remove('active');
        } else {
            labelDemo.classList.add('active');
            labelLive.classList.remove('active');
        }
    }

    // Call settings defaults load for scraper dashboard
    if (document.getElementById('report_dept')) {
        loadScraperDefaults();
    }
});

// Delete analysis helper (specifically for templates/history.html)
async function deleteHistory(type, id) {
    if (!confirm('Are you sure you want to delete this record?')) return;
    try {
        const url = type === 'analysis' ? `/api/history/delete_analysis/${id}` : `/api/history/delete_timetable/${id}`;
        const res = await fetch(url, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            if (typeof loadAllHistory === 'function') loadAllHistory();
        } else {
            alert('Failed to delete history record.');
        }
    } catch (e) {
        alert('Delete failed.');
    }
}
