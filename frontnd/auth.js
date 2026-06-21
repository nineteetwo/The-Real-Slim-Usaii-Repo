/* =============================================
   Auth Scripts (Login & Signup)
============================================= */
document.addEventListener('DOMContentLoaded', function () {
    /* ========== Utility: password toggle ========== */
    function setupPasswordToggle(toggleId, fieldId) {
        var btn = document.getElementById(toggleId);
        var field = document.getElementById(fieldId);
        if (!btn || !field) return;

        btn.addEventListener('click', function () {
            var icon = btn.querySelector('i');
            if (field.type === 'password') {
                field.type = 'text';
                icon.classList.remove('fa-eye');
                icon.classList.add('fa-eye-slash');
            } else {
                field.type = 'password';
                icon.classList.remove('fa-eye-slash');
                icon.classList.add('fa-eye');
            }
        });
    }

    /* Login page toggle */
    setupPasswordToggle('toggle-password', 'login-password');

    /* Signup page toggle */
    setupPasswordToggle('toggle-signup-password', 'signup-password');

    /* ========== Login form submit ========== */
    var loginForm = document.getElementById('login-form');
    var loginEmailInput = document.getElementById('login-email');
    var loginPasswordInput = document.getElementById('login-password');
    var loginError = document.getElementById('login-error');

    if (loginForm) {
        loginForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            var email = loginEmailInput ? loginEmailInput.value.trim() : '';
            var password = loginPasswordInput ? loginPasswordInput.value : '';

            try {
                var response = await fetch('http://127.0.0.1:8000/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: email,
                        password: password
                    })
                });

                var data = await response.json();

                if (response.ok) {
                    // Storing Name for the UI layout and Email for the database tracker
                    localStorage.setItem('calhelpr_name', data.name); 
                    localStorage.setItem('calhelpr_email', data.email); 

                    // Send them straight back to the main chat console view
                    window.location.href = 'index.html';
                } else {
                    // Unpack error text instead of throwing an object alert
                    var errorText = data.detail && typeof data.detail === 'object' 
                        ? "Please check your login format inputs." 
                        : (data.detail || "Invalid email or password.");

                    if (loginError) {
                        loginError.innerText = errorText;
                    } else {
                        alert(errorText);
                    }
                }
            } catch (error) {
                console.error("Login verification network exception:", error);
                if (loginError) loginError.innerText = "Cannot connect to backend auth service.";
            }
        });
    }

    /* ========== Signup form submit ========== */
    var signupForm = document.getElementById('signup-form');
    var signupNameInput = document.getElementById('signup-name');   
    var signupEmailInput = document.getElementById('signup-email'); 
    var signupPasswordInput = document.getElementById('signup-password');
    var signupConfirmInput = document.getElementById('signup-confirm');
    var signupError = document.getElementById('signup-error');

    if (signupForm) {
        signupForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            var name = signupNameInput ? signupNameInput.value.trim() : '';
            var email = signupEmailInput ? signupEmailInput.value.trim() : '';
            var password = signupPasswordInput ? signupPasswordInput.value : '';
            var confirm = signupConfirmInput ? signupConfirmInput.value : '';

            // Client-side local structural identity validation match check
            if (password !== confirm) {
                if (signupConfirmInput) {
                    signupConfirmInput.setCustomValidity('Passwords do not match.');
                    signupConfirmInput.reportValidity();
                }
                return;
            }

            try {
                var response = await fetch('http://127.0.0.1:8000/api/signup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: name,
                        email: email,
                        password: password
                    })
                });

                var data = await response.json();

                if (response.ok) {
                    alert("Account registered successfully! Please log in.");
                    window.location.href = 'login.html';
                } else {
                    // Check if data.detail is an array/object or a string in error message
                    var errorText = "Signup failed.";
                    if (data.detail) {
                        if (typeof data.detail === 'string') {
                            errorText = data.detail;
                        } else if (Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) {
                            // Unpacks specific FastAPI validation messages like "Field required"
                            errorText = data.detail[0].msg; 
                        } else if (typeof data.detail === 'object') {
                            errorText = "Invalid input details provided.";
                        }
                    }

                    if (signupError) {
                        signupError.innerText = errorText;
                    } else {
                        alert(errorText); // Convert to human-readable text
                    }
                }
            } catch (error) {
                console.error("Signup account creation failure:", error);
                if (signupError) signupError.innerText = "Cannot connect to backend registration endpoint.";
            }
        });

        /* Clear custom validity when user types */
        if (signupConfirmInput) {
            signupConfirmInput.addEventListener('input', function () {
                signupConfirmInput.setCustomValidity('');
            });
        }
    }
});
