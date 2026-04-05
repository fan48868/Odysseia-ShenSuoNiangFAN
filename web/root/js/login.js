const tokenInput = document.getElementById('token');
const loginButton = document.getElementById('loginBtn');
const rememberToggle = document.getElementById('rememberToken');
const rememberedTokenKey = 'webuiRememberedToken';

async function loginWithToken(token, rememberToken) {
    if (!token) {
        alert('请输入管理员口令。');
        return false;
    }

    const response = await fetch('/login', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ token })
    });

    if (!response.ok) {
        throw new Error('登录失败，请检查口令。');
    }

    sessionStorage.setItem('adminToken', token);
    if (rememberToken) {
        localStorage.setItem(rememberedTokenKey, token);
    } else {
        localStorage.removeItem(rememberedTokenKey);
    }

    window.location.href = '/main';
    return true;
}

loginButton.addEventListener('click', async () => {
    try {
        await loginWithToken(tokenInput.value.trim(), rememberToggle.checked);
    } catch (error) {
        alert(error.message);
    }
});

tokenInput.addEventListener('keydown', async (event) => {
    if (event.key !== 'Enter') {
        return;
    }

    event.preventDefault();
    try {
        await loginWithToken(tokenInput.value.trim(), rememberToggle.checked);
    } catch (error) {
        alert(error.message);
    }
});

window.addEventListener('DOMContentLoaded', async () => {
    const rememberedToken = localStorage.getItem(rememberedTokenKey);
    if (!rememberedToken) {
        return;
    }

    tokenInput.value = rememberedToken;
    rememberToggle.checked = true;

    try {
        await loginWithToken(rememberedToken, true);
    } catch (error) {
        localStorage.removeItem(rememberedTokenKey);
    }
});
