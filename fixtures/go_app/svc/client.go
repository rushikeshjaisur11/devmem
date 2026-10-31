package svc

import "example.com/goapp/retry"

// Client talks to the user API.
type Client struct{ base string }

// Get fetches a path with retries.
func (c *Client) Get(path string) error {
	return retry.Do(3, func() error { return c.raw(path) })
}

func (c *Client) raw(path string) error { return nil }

// LoadUser loads one user.
func LoadUser(c *Client, id string) error { return c.Get("/users/" + id) }
