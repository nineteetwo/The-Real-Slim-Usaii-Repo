async function handleStatusSubmit(e) {
    e.preventDefault();

    const selectedProgram = document.getElementById('program-select').value;
    const selectedStatus = document.getElementById('status-select').value;
    
    const userEmail = localStorage.getItem('calhelpr_email') || 'user@example.com';
    const activeThread = localStorage.getItem('calhelpr_runtime_thread') || 'thread_main';

    try {
        // Update the Dropdown / Application State in DB
        const trackerResponse = await fetch('http://127.0.0.1:8000/api/tracker/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: userEmail,
                thread_id: activeThread,
                program_name: selectedProgram,
                status: selectedStatus
            })
        });

        const trackerData = await trackerResponse.json();

        // If status is 'Rejected', silently invoke the scraping pipeline right now!
        if (trackerResponse.ok && trackerData.trigger_followup) {
            console.log("Rejection detected! Activating live community resource scraper...");
            
            await fetch('http://127.0.0.1:8000/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: `[System Event: Application Rejected from ${selectedProgram}]`,
                    email: userEmail,
                    thread_id: activeThread
                })
            });
            window.location.href = 'chat.html';
        }

    } catch (error) {
        console.error("Error running tracking updates:", error);
    }
}