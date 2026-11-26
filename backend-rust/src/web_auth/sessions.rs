use super::BrowserSession;
use ring::rand::{SecureRandom, SystemRandom};
use std::collections::{HashMap, VecDeque};
use std::fmt::Write;
use std::io;
use std::net::IpAddr;
use std::time::{Duration, Instant};
use subtle::ConstantTimeEq;

pub(super) const SESSION_LIFETIME: Duration = Duration::from_secs(12 * 60 * 60);
const CHALLENGE_LIFETIME: Duration = Duration::from_secs(15 * 60);
const MAX_CHALLENGES: usize = 4096;
const MAX_SOURCE_CHALLENGES: usize = 64;
const MAX_SESSIONS: usize = 2048;
const RATE_WINDOW: Duration = Duration::from_secs(60);
const FAILURE_LOCK: Duration = Duration::from_secs(300);
const MAX_PASSWORD_IPS: usize = 20_000;

#[derive(Clone)]
pub(super) struct Authenticated {
    pub generation: String,
    pub password_enabled: bool,
    pub browser: BrowserSession,
}

#[derive(Clone)]
pub(super) struct Session {
    pub csrf: String,
    pub authenticated: Option<Authenticated>,
    expires: Instant,
    source: Option<IpAddr>,
}

impl Session {
    pub fn matches_csrf(&self, provided: &str) -> bool {
        bool::from(self.csrf.as_bytes().ct_eq(provided.as_bytes()))
    }
}

#[derive(Default)]
pub(super) struct Sessions {
    sessions: HashMap<String, Session>,
}

impl Sessions {
    pub fn get(&mut self, id: &str) -> Option<Session> {
        let session = self.sessions.get(id)?;
        if session.expires <= Instant::now() {
            self.sessions.remove(id);
            return None;
        }
        Some(session.clone())
    }

    pub fn remove(&mut self, id: Option<&str>) {
        if let Some(id) = id {
            self.sessions.remove(id);
        }
    }

    pub fn issue_challenge(
        &mut self,
        old: Option<&str>,
        source: Option<IpAddr>,
        id: String,
        csrf: String,
    ) -> bool {
        self.prune();
        let challenges = self
            .sessions
            .values()
            .filter(|session| session.authenticated.is_none());
        if challenges.clone().count() >= MAX_CHALLENGES
            || challenges
                .filter(|session| session.source == source)
                .count()
                >= MAX_SOURCE_CHALLENGES
        {
            return false;
        }
        self.remove(old);
        self.sessions.insert(
            id,
            Session {
                csrf,
                authenticated: None,
                expires: Instant::now() + CHALLENGE_LIFETIME,
                source,
            },
        );
        true
    }

    pub fn rotate(
        &mut self,
        old: &str,
        old_csrf: &str,
        id: String,
        csrf: String,
        authenticated: Authenticated,
    ) -> Result<(), RotateError> {
        self.prune();
        let incoming = self
            .sessions
            .get(old)
            .filter(|session| session.matches_csrf(old_csrf))
            .ok_or(RotateError::InvalidChallenge)?;
        let active = self
            .sessions
            .values()
            .filter(|session| session.authenticated.is_some())
            .count();
        if active >= MAX_SESSIONS && incoming.authenticated.is_none() {
            return Err(RotateError::SessionLimit);
        }
        self.sessions.remove(old);
        self.sessions.insert(
            id,
            Session {
                csrf,
                authenticated: Some(authenticated),
                expires: Instant::now() + SESSION_LIFETIME,
                source: None,
            },
        );
        Ok(())
    }

    fn prune(&mut self) {
        let now = Instant::now();
        self.sessions.retain(|_, session| session.expires > now);
    }
}

pub(super) enum RotateError {
    InvalidChallenge,
    SessionLimit,
}

#[derive(Default)]
pub(super) struct PasswordLimits {
    all: VecDeque<Instant>,
    clients: HashMap<Option<IpAddr>, PasswordClient>,
}

#[derive(Default)]
struct PasswordClient {
    attempts: VecDeque<Instant>,
    failures: u8,
    blocked_until: Option<Instant>,
}

impl PasswordLimits {
    pub fn admit(&mut self, client: Option<IpAddr>) -> Result<(), u64> {
        let now = Instant::now();
        if let Some(wait) = retry_after(&mut self.all, 30, now) {
            return Err(wait);
        }
        if !self.clients.contains_key(&client) && self.clients.len() >= MAX_PASSWORD_IPS {
            self.clients.retain(|_, state| {
                state.blocked_until.is_some_and(|until| until > now)
                    || state
                        .attempts
                        .back()
                        .is_some_and(|last| now.duration_since(*last) < RATE_WINDOW)
            });
        }
        let state = self.clients.entry(client).or_default();
        if let Some(wait) = retry_after(&mut state.attempts, 10, now) {
            return Err(wait);
        }
        state.attempts.push_back(now);
        self.all.push_back(now);
        if let Some(until) = state.blocked_until.filter(|until| *until > now) {
            return Err(round_up(until.duration_since(now)));
        }
        Ok(())
    }

    pub fn record(&mut self, client: Option<IpAddr>, accepted: bool) {
        let state = self.clients.entry(client).or_default();
        if accepted {
            state.failures = 0;
            state.blocked_until = None;
        } else {
            state.failures += 1;
            if state.failures >= 5 {
                state.failures = 0;
                state.blocked_until = Some(Instant::now() + FAILURE_LOCK);
            }
        }
    }
}

fn retry_after(attempts: &mut VecDeque<Instant>, limit: usize, now: Instant) -> Option<u64> {
    while attempts
        .front()
        .is_some_and(|old| now.duration_since(*old) >= RATE_WINDOW)
    {
        attempts.pop_front();
    }
    attempts
        .front()
        .filter(|_| attempts.len() >= limit)
        .map(|first| round_up(RATE_WINDOW - now.duration_since(*first)))
}

fn round_up(duration: Duration) -> u64 {
    duration.as_secs() + u64::from(duration.subsec_nanos() != 0)
}

pub(super) fn new_secret() -> io::Result<String> {
    let mut bytes = [0; 32];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| io::Error::other("Secure random generator unavailable"))?;
    let mut value = String::with_capacity(64);
    for byte in bytes {
        write!(&mut value, "{byte:02x}").expect("formatting a String does not fail");
    }
    Ok(value)
}
