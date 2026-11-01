package retry

// Do calls fn up to n times until it succeeds.
func Do(n int, fn func() error) error {
	var err error
	for i := 0; i < n; i++ {
		if err = fn(); err == nil {
			return nil
		}
	}
	return err
}
